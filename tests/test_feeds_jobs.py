"""Scheduled jobs offline: refresh writes nothing, the weekly check reads without crashing."""

from __future__ import annotations

from optionslab.feeds import jobs


def test_refresh_all_refuses_offline(offline_home):
    env = jobs.refresh_all(["TEST"])
    assert env["data"] is None and "offline" in env["warnings"][0]


def test_weekly_check_offline_reports_gaps_not_numbers(offline_home):
    env = jobs.weekly_check()
    assert env["data"]["alerts"] == []
    assert env["not_verified"]            # nothing snapshotted -> every read says so
