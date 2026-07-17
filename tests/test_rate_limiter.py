import threading
import time

from agent_115.client import GlobalRateLimiter


def test_limiter_serializes_requests_and_enforces_interval():
    limiter = GlobalRateLimiter(qps=0.5)
    active = 0
    maximum = 0
    starts = []
    guard = threading.Lock()

    def worker():
        nonlocal active, maximum
        with limiter:
            with guard:
                active += 1
                maximum = max(maximum, active)
                starts.append(time.monotonic())
            time.sleep(0.02)
            with guard:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert maximum == 1
    assert len(starts) == 3
    assert starts[1] - starts[0] >= 1.95
    assert starts[2] - starts[1] >= 1.95


def test_limiter_can_update_qps():
    limiter = GlobalRateLimiter(qps=1)
    limiter.set_qps(5)
    assert limiter.qps == 5


def test_limiter_rejects_non_positive_qps():
    limiter = GlobalRateLimiter(qps=1)
    try:
        limiter.set_qps(0)
    except ValueError:
        pass
    else:
        raise AssertionError("non-positive qps must be rejected")
