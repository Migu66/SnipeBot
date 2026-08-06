from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from storage import Storage


def test_new_listing_is_not_notified(storage, make_listing):
    listing = make_listing()
    assert storage.is_notified(listing.platform, listing.id) is False


def test_mark_notified_then_is_notified(storage, make_listing):
    listing = make_listing()
    storage.mark_notified(listing)
    assert storage.is_notified(listing.platform, listing.id) is True


def test_filter_new_excludes_already_notified(storage, make_listing):
    already = make_listing(id="already")
    fresh = make_listing(id="fresh")
    storage.mark_notified(already)

    result = storage.filter_new([already, fresh])

    assert [listing.id for listing in result] == ["fresh"]


def test_filter_new_distinguishes_platforms_with_same_id(storage, make_listing):
    wallapop_listing = make_listing(id="123", platform="wallapop")
    vinted_listing = make_listing(id="123", platform="vinted")
    storage.mark_notified(wallapop_listing)

    result = storage.filter_new([wallapop_listing, vinted_listing])

    assert [listing.platform for listing in result] == ["vinted"]


def test_filter_new_with_empty_list(storage):
    assert storage.filter_new([]) == []


def test_idempotent_across_reopening_same_db(tmp_path, make_listing):
    db_path = tmp_path / "snipebot.db"
    listing = make_listing()

    with Storage(db_path) as first_run:
        new_listings = first_run.filter_new([listing])
        assert len(new_listings) == 1
        first_run.mark_notified(listing)

    with Storage(db_path) as second_run:
        new_listings = second_run.filter_new([listing])
        assert new_listings == []


def test_purge_older_than_removes_old_rows(tmp_path, make_listing):
    db_path = tmp_path / "snipebot.db"
    listing = make_listing()

    with Storage(db_path) as st:
        st.mark_notified(listing)
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        st._conn.execute(
            "UPDATE notified_listings SET notified_at = ? WHERE id = ?",
            (old_timestamp, listing.id),
        )
        st._conn.commit()

        removed = st.purge_older_than(days=30)

        assert removed == 1
        assert st.is_notified(listing.platform, listing.id) is False


def test_purge_older_than_keeps_recent_rows(storage, make_listing):
    listing = make_listing()
    storage.mark_notified(listing)

    removed = storage.purge_older_than(days=30)

    assert removed == 0
    assert storage.is_notified(listing.platform, listing.id) is True


def test_creates_parent_directory(tmp_path, make_listing):
    db_path = tmp_path / "nested" / "dir" / "snipebot.db"
    with Storage(db_path) as st:
        st.mark_notified(make_listing())
    assert db_path.exists()


def test_read_only_without_existing_db_treats_everything_as_new(tmp_path, make_listing):
    db_path = tmp_path / "does_not_exist.db"
    listing = make_listing()

    with Storage(db_path, read_only=True) as st:
        result = st.filter_new([listing])

    assert result == [listing]
    assert not db_path.exists()


def test_read_only_does_not_write_to_existing_db(tmp_path, make_listing):
    db_path = tmp_path / "snipebot.db"
    listing = make_listing()

    with Storage(db_path) as st:
        pass  # crea el esquema sin marcar nada

    with Storage(db_path, read_only=True) as st:
        with pytest.raises(RuntimeError):
            st.mark_notified(listing)
