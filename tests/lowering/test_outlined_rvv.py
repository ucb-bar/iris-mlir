"""Output ownership stays disjoint and exhaustive through exact row/column tails."""

import pytest

from iris.lower.outlined_rvv import partition


@pytest.mark.parametrize(
    "m,n,te,slots",
    [(3, 65, 32, (0, 1)), (64, 128, 32, (0, 1)), (17, 33, 16, (0,)), (1, 1, 16, (1,))],
)
def test_partition_covers_all_cells_and_preserves_block_ownership(m, n, te, slots):
    jobs = partition(m, n, te, slots, job_budget=512)
    cells = {}
    last = {}
    columns = (n + 2 * te - 1) // (2 * te)
    for job in jobs:
        assert job["m"] & (job["m"] - 1) == job["n"] & (job["n"] - 1) == 0
        assert job["slot"] == sorted(slots)[job["block"] % len(slots)]
        assert job["block"] == (job["row"] // (2 * te)) * columns + job["col"] // (
            2 * te
        )
        for i in range(job["row"], job["row"] + job["m"]):
            for j in range(job["col"], job["col"] + job["n"]):
                assert (i, j) not in cells
                cells[(i, j)] = job["slot"]
        last[job["block"]] = last.get(job["block"], 0) + int(job["last"])
    assert set(cells) == {(i, j) for i in range(m) for j in range(n)}
    assert set(last.values()) == {1}


def test_partition_budget_and_unknown_worker_refuse():
    with pytest.raises(ValueError, match="budget"):
        partition(3, 65, 32, (0, 1), job_budget=3)
    with pytest.raises(ValueError, match="slots"):
        partition(3, 65, 32, (2,), job_budget=64)
