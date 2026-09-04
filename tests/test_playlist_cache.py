from concurrent.futures import ThreadPoolExecutor
import threading

from cloudmusic2ktv.playlist_cache import PlaylistCache


def test_playlist_list_uses_absolute_ttl_and_manual_invalidation():
    now = [100.0]
    loads = []
    cache = PlaylistCache(ttl_seconds=10, clock=lambda: now[0])

    def load():
        loads.append(len(loads) + 1)
        return [{"id": loads[-1]}]

    assert cache.get_playlists("7", load)[0]["id"] == 1
    now[0] = 109.0
    assert cache.get_playlists("7", load)[0]["id"] == 1
    now[0] = 110.0
    assert cache.get_playlists("7", load)[0]["id"] == 2
    cache.invalidate_user("7")
    assert cache.get_playlists("7", load)[0]["id"] == 3


def test_track_pages_and_search_share_compact_song_data():
    cache = PlaylistCache()
    detail_loads = []
    song_loads = []

    def detail():
        detail_loads.append(True)
        return {"id": 12, "name": "歌单"}, [1, 2, 3]

    songs = {
        1: {"id": 1, "name": "夜曲", "artist": "周杰伦", "album": "十一月的萧邦", "source": {"large": True}},
        2: {"id": 2, "name": "晴天", "artist": "周杰伦", "album": "叶惠美", "source": {"large": True}},
        3: {"id": 3, "name": "海阔天空", "artist": "Beyond", "album": "乐与怒", "source": {"large": True}},
    }

    def load_songs(song_ids):
        song_loads.append(list(song_ids))
        return [songs[song_id] for song_id in song_ids]

    page = cache.get_tracks(
        "7", 12, offset=0, limit=1, query="", detail_loader=detail, song_loader=load_songs
    )
    result = cache.get_tracks(
        "7", 12, offset=0, limit=50, query="beyond 乐与怒", detail_loader=detail, song_loader=load_songs
    )
    repeated = cache.get_tracks(
        "7", 12, offset=0, limit=50, query="周杰伦", detail_loader=detail, song_loader=load_songs
    )

    assert [song["id"] for song in page["songs"]] == [1]
    assert [song["id"] for song in result["songs"]] == [3]
    assert [song["id"] for song in repeated["songs"]] == [1, 2]
    assert detail_loads == [True]
    assert song_loads == [[1], [2, 3]]
    assert "source" not in page["songs"][0]


def test_track_cache_is_isolated_by_user_and_uses_lru_eviction():
    cache = PlaylistCache(max_playlists=2)
    detail_loads = []

    def read(user_id, playlist_id):
        def detail():
            detail_loads.append((user_id, playlist_id))
            return {"id": playlist_id}, []

        return cache.get_tracks(
            user_id,
            playlist_id,
            offset=0,
            limit=10,
            query="",
            detail_loader=detail,
            song_loader=lambda _: [],
        )

    read("a", 1)
    read("a", 2)
    read("a", 1)  # Playlist 2 is now the least recently used entry.
    read("b", 1)
    read("a", 2)

    assert detail_loads == [("a", 1), ("a", 2), ("b", 1), ("a", 2)]


def test_concurrent_searches_share_one_index_build():
    cache = PlaylistCache()
    started = threading.Event()
    release = threading.Event()
    counts = {"detail": 0, "songs": 0}

    def detail():
        counts["detail"] += 1
        return {"id": 12}, [1, 2]

    def load_songs(song_ids):
        counts["songs"] += 1
        started.set()
        assert release.wait(2)
        return [{"id": song_id, "name": f"song {song_id}", "artist": "", "album": ""} for song_id in song_ids]

    def search():
        return cache.get_tracks(
            "7", 12, offset=0, limit=50, query="song", detail_loader=detail, song_loader=load_songs
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(search)
        assert started.wait(2)
        second = executor.submit(search)
        release.set()
        assert first.result(timeout=2)["total"] == 2
        assert second.result(timeout=2)["total"] == 2

    assert counts == {"detail": 1, "songs": 1}
