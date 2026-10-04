"""Hand-shape and trajectory features, plus DTW matching for recorded movements."""
import numpy as np

PALM = [0, 5, 9, 13, 17]
N_RESAMPLE = 16


def hand_scale(lm):
    return float(np.linalg.norm(lm[9, :2] - lm[0, :2])) + 1e-6


def palm_center(lm):
    return lm[PALM, :2].mean(0)


def pose_vec(lm):
    """Landmarks relative to the wrist, in hand-size units: (21, 2)."""
    return (lm[:, :2] - lm[0, :2]) / hand_scale(lm)


def pose_dist(a, b):
    return float(np.linalg.norm(a - b, axis=-1).mean())


FINGERS = {"index": (5, 6, 8), "middle": (9, 10, 12), "ring": (13, 14, 16), "pinky": (17, 18, 20)}


def extended(lm, finger):
    """Is this finger straight? (tip clearly further from the wrist than its middle joint)"""
    _, pip, tip = FINGERS[finger]
    w = lm[0, :2]
    return np.linalg.norm(lm[tip, :2] - w) > 1.25 * np.linalg.norm(lm[pip, :2] - w)


def fist_ratio(lm):
    """Mean fingertip-to-wrist distance in hand sizes: ~2 open, <1.3 fist."""
    return float(np.linalg.norm(lm[[8, 12, 16, 20], :2] - lm[0, :2], axis=1).mean()) / hand_scale(lm)


def _d3(a, b):
    """3D distance; MediaPipe depth is noisier, so it counts half."""
    d = a - b
    return float(np.sqrt(d[0] ** 2 + d[1] ** 2 + (0.5 * d[2]) ** 2))


def pinch_ratio(lm, finger_tip=8):
    """Thumb tip to a fingertip (index by default), in hand sizes, using depth too so
    fingers that merely overlap from the camera's view don't count as touching."""
    return _d3(lm[4], lm[finger_tip]) / (_d3(lm[9], lm[0]) + 1e-6)


def palm_speed(frames, window=0.15):
    """Palm speed in hand-sizes per second over the last `window` seconds."""
    if len(frames) < 2:
        return 0.0
    t1, lm1 = frames[-1]
    for t0, lm0 in reversed(frames):
        if t1 - t0 >= window:
            break
    if t1 - t0 <= 1e-3:
        return 0.0
    d = np.linalg.norm(palm_center(lm1) - palm_center(lm0)) / hand_scale(lm1)
    return float(d / (t1 - t0))


def pose_speed(frames, window=0.15):
    """How fast the hand *shape* changes (hand-sizes per second), e.g. fist opening."""
    if len(frames) < 2:
        return 0.0
    t1, lm1 = frames[-1]
    for t0, lm0 in reversed(frames):
        if t1 - t0 >= window:
            break
    if t1 - t0 <= 1e-3:
        return 0.0
    return pose_dist(pose_vec(lm1), pose_vec(lm0)) / (t1 - t0)


def activity(frames):
    """Combined movement: palm travel + shape change (two hands: the busier one)."""
    if frames and frames[0][1].ndim == 3:
        return max(activity(f) for f in split_pair(frames))
    return palm_speed(frames) + 1.5 * pose_speed(frames)


def motion_amount(frames):
    """Size of a movement: palm path length, or how far the shape travelled.
    Two hands: also how much the gap between them changed."""
    if len(frames) < 2:
        return 0.0
    if frames[0][1].ndim == 3:
        a, b = split_pair(frames)
        gap = [np.linalg.norm(palm_center(y) - palm_center(x)) / hand_scale(x)
               for (_, x), (_, y) in zip(a, b)]
        return max(motion_amount(a), motion_amount(b), max(gap) - min(gap))
    p0 = pose_vec(frames[0][1])
    shape = max(pose_dist(pose_vec(lm), p0) for _, lm in frames)
    return max(path_length(frames), 2.0 * shape)


def path_length(frames):
    if len(frames) < 2:
        return 0.0
    c = np.array([palm_center(lm) for _, lm in frames])
    s = np.median([hand_scale(lm) for _, lm in frames])
    return float(np.linalg.norm(np.diff(c, axis=0), axis=1).sum() / s)


def _resample(u, grid, arr):
    flat = arr.reshape(len(arr), -1)
    out = np.stack([np.interp(grid, u, flat[:, k]) for k in range(flat.shape[1])], 1)
    return out.reshape((len(grid),) + arr.shape[1:])


def seq_features(frames, n=N_RESAMPLE):
    """Resample a movement to n steps of (hand shape, trajectory).

    One hand: frames hold (21, 3) arrays; trajectory = palm path.
    Two hands: frames hold (2, 21, 3) arrays (left-most hand first); trajectory =
    the vector between the hands (that's what 'expand' is about) + their midpoint path.
    """
    ts = np.array([t for t, _ in frames], np.float64)
    lms = np.stack([lm for _, lm in frames])
    u = (ts - ts[0]) / max(ts[-1] - ts[0], 1e-6)
    grid = np.linspace(0, 1, n)
    if lms.ndim == 3:
        scale = np.median([hand_scale(lm) for lm in lms])
        centers = lms[:, PALM, :2].mean(1)
        traj = (centers - centers[0]) / scale
        poses = np.stack([pose_vec(lm) for lm in lms])
    else:
        scale = np.median([(hand_scale(a) + hand_scale(b)) / 2 for a, b in lms])
        ca, cb = lms[:, 0, PALM, :2].mean(1), lms[:, 1, PALM, :2].mean(1)
        mid = (ca + cb) / 2
        traj = np.concatenate([(cb - ca) / scale, (mid - mid[0]) / scale], 1)
        poses = np.stack([np.concatenate([pose_vec(a), pose_vec(b)]) for a, b in lms])
    return _resample(u, grid, poses), _resample(u, grid, traj)


def split_pair(frames):
    """Two-hand frames -> (left-hand frames, right-hand frames)."""
    return [(t, lm[0]) for t, lm in frames], [(t, lm[1]) for t, lm in frames]


def dtw(cost):
    n, m = cost.shape
    band = max(2, n // 4)
    inf = float("inf")
    D = [[inf] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    c = cost.tolist()
    for i in range(1, n + 1):
        row, prev, ci = D[i], D[i - 1], c[i - 1]
        for j in range(max(1, i - band), min(m, i + band) + 1):
            row[j] = ci[j - 1] + min(prev[j - 1], prev[j], row[j - 1])
    return D[n][m] / n


def seq_dist(a, b, traj_weight):
    pa, ta = a
    pb, tb = b
    pose_c = np.linalg.norm(pa[:, None] - pb[None], axis=-1).mean(-1)
    traj_c = np.linalg.norm(ta[:, None] - tb[None], axis=-1)
    return dtw(pose_c + traj_weight * traj_c)
