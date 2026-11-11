import asyncio

import pytest

from espncricinfo._browser import SharedBrowser


def test_plan_pages_uses_fewest_tabs_that_fit_budget():
    b = SharedBrowser()
    b.avg_fetch_seconds = 2.0
    assert b.plan_pages(0, 30, 4) == 1
    assert b.plan_pages(3, 30, 4) == 1  # 6 s on one tab
    assert b.plan_pages(15, 30, 4) == 1  # exactly 30 s
    assert b.plan_pages(16, 30, 4) == 2
    assert b.plan_pages(100, 30, 4) == 4  # capped
    assert b.plan_pages(2, 1, 4) == 2  # never more tabs than fetches


def test_fetch_time_average_starts_from_first_sample():
    b = SharedBrowser()
    b._record_fetch_time(4.0)
    assert b.avg_fetch_seconds == pytest.approx(4.0)
    b._record_fetch_time(1.0)
    assert b.avg_fetch_seconds == pytest.approx(3.1)


def test_tab_limit_can_change_while_fetches_wait():
    b = SharedBrowser(max_pages=1)

    async def scenario():
        await b._acquire_slot()
        waiter = asyncio.create_task(b._acquire_slot())
        await asyncio.sleep(0.01)
        assert not waiter.done()  # blocked by the 1-tab limit
        await b._apply_max_pages(2)
        await asyncio.wait_for(waiter, 1)  # raising the limit lets it in
        assert b._in_use == 2
        await b._release_slot()
        await b._release_slot()
        assert b._in_use == 0

    try:
        b.run(scenario())
    finally:
        b.close()
