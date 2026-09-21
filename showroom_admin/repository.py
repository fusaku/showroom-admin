from __future__ import annotations

import copy
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


class DataUnavailable(Exception):
    """Safe public error; connection details must never reach the browser."""


def iso(value):
    return value.isoformat() if value is not None else None


def now():
    return datetime.now(timezone.utc)


class DemoRepository:
    mode = "demo"

    def __init__(self):
        stamp = now()
        self._seed_time = stamp
        groups = ["AKB48", "SKE48", "NMB48", "HKT48", "NGT48", "STU48"]
        names = [("青木 さくら", "Sakura Aoki"), ("水野 はる", "Haru Mizuno"),
                 ("星野 りん", "Rin Hoshino"), ("白石 みお", "Mio Shiraishi"),
                 ("森川 なな", "Nana Morikawa"), ("小川 ゆい", "Yui Ogawa"),
                 ("高橋 あおい", "Aoi Takahashi"), ("藤井 れな", "Rena Fujii")]
        self.rows = []
        for i in range(36):
            jp, en = names[i % len(names)]
            live = i in (0, 2, 5, 8, 13)
            checked = stamp - timedelta(seconds=1800 if i == 13 else 8 + i)
            self.rows.append(dict(id=i + 1, member_id=f"demo_member_{i + 1:03d}",
                name_jp=f"{jp} {i // 8 + 1}" if i >= 8 else jp, name_en=en,
                group_name=groups[i % 6], team="Demo team", room_id=f"{900000 + i}",
                room_url_key=f"demo_{i + 1}", enabled=0 if i in (9, 17, 25) else 1,
                is_live=1 if live else 0, check_time=iso(checked),
                started_at=iso(stamp - timedelta(minutes=12 + i * 3)) if live else None,
                assignment_count=1 if live else 0,
                updated_at=iso(stamp - timedelta(days=2))))
        self.nodes = [dict(instance_id="demo-monitor-a", instance_type="monitor", display_name="直播检测",
            status="active", max_capacity=100, current_load=0, available_capacity=100,
            load_percent=0, last_heartbeat=iso(stamp - timedelta(seconds=10))),
            dict(instance_id="demo-recorder-3c", instance_type="recorder", display_name="3C · 录制环境",
            status="active", max_capacity=30, current_load=5, available_capacity=25,
            load_percent=16.7, last_heartbeat=iso(stamp - timedelta(seconds=20)))]

    def all_rows(self):
        rows = copy.deepcopy(self.rows)
        journal = getattr(self, '_creation_journal', None)
        if journal:
            import json
            with journal.connect() as db:
                added = db.execute('SELECT * FROM demo_members ORDER BY id DESC').fetchall()
            for item in added:
                payload = json.loads(item['payload'])
                rows.append(dict(payload, id=1000000+item['id'], enabled=item['enabled'],
                    is_live=None, check_time=None, started_at=None, assignment_count=0,
                    updated_at=item['created_at']))
        if journal:
            with journal.connect() as db:overrides={r['member_id']:__import__('json').loads(r['payload']) for r in db.execute('SELECT * FROM demo_overrides')}
            for row in rows:
                if row['member_id'] in overrides:row.update(overrides[row['member_id']])
        return rows

    def snapshot(self, value):
        """Advance only synthetic timestamps; preserve the deliberately stale example."""
        delta = now() - self._seed_time
        def shift(item):
            if isinstance(item, list):
                return [shift(v) for v in item]
            if isinstance(item, dict):
                return {k: iso(datetime.fromisoformat(v) + delta)
                        if k in ("check_time", "started_at", "last_heartbeat", "updated_at") and v
                        else shift(v) for k, v in item.items()}
            return item
        return shift(value)

    def groups(self):
        return sorted({r["group_name"] for r in self.rows})

    def members(self, search="", group="", enabled="", page=1, page_size=12):
        rows = [r for r in self.all_rows() if (not group or r["group_name"] == group)
                and (enabled == "" or str(r["enabled"]) == enabled)
                and (not search or search.casefold() in " ".join(str(r[k]) for k in
                     ("member_id", "name_jp", "name_en", "room_id")).casefold())]
        rows.sort(key=lambda r: (0 if r["is_live"] == 1 else 1, r["id"]))
        return dict(items=self.snapshot(rows[(page - 1) * page_size:page * page_size]), total=len(rows))

    def live(self):
        return self.snapshot(sorted((r for r in self.rows if r["is_live"] == 1),
                                    key=lambda r: r["started_at"] or "", reverse=True))

    def instances(self):
        return self.snapshot(self.nodes)

    def overview(self):
        return dict(total_members=len(self.all_rows()), enabled_members=sum(r["enabled"] == 1 for r in self.all_rows()),
            live=self.live(), instances=self.instances(), observed_at=iso(now()))

    def detail(self, member_id):
        row = next((r for r in self.all_rows() if r["member_id"] == member_id), None)
        if row is None:
            return None
        result = self.snapshot(row)
        if row['id'] >= 1000000 or 'youtube' in row:
            y=row.get('youtube')
            result.update(youtube={k:v for k,v in y.items() if k!='tags'} if y else None, tags=y.get('tags',[]) if y else [], assignments=[], history=[])
            result['updated_at']=row['updated_at']
            return result
        result.update(youtube=dict(privacy_status="public", category_id="22", playlist_id="",
                                  use_primary_account=1, title_template="", description_template=""),
            tags=["演示数据"], assignments=[dict(instance_id="demo-recorder-3c", instance_type="recorder",
             enabled=1, assigned_by="auto-on-live")] if row["is_live"] else [], history=[])
        return result


class OracleRepository:
    mode = "oracle"
    # No recorder imports, no table creation, no INSERT/UPDATE/DELETE or commit.
    member_select = """
        SELECT m.ID, m.MEMBER_ID, m.NAME_JP, m.NAME_EN, g.NAME AS GROUP_NAME,
               m.TEAM, m.ROOM_ID, m.ROOM_URL_KEY, m.ENABLED, m.UPDATED_AT,
               ls.IS_LIVE, ls.STARTED_AT, ls.CHECK_TIME,
               (SELECT COUNT(*) FROM ADMIN.MEMBER_INSTANCES mi
                 WHERE mi.MEMBER_ID=m.ID AND mi.INSTANCE_TYPE='recorder' AND mi.ENABLED=1) AS ASSIGNMENT_COUNT
        FROM ADMIN.MEMBERS m JOIN ADMIN.GROUPS g ON g.ID=m.GROUP_ID
        LEFT JOIN (SELECT MEMBER_ID, IS_LIVE, STARTED_AT, CHECK_TIME FROM (
          SELECT s.*, ROW_NUMBER() OVER (PARTITION BY MEMBER_ID ORDER BY CHECK_TIME DESC, ID DESC) AS RN
          FROM ADMIN.LIVE_STATUS s) WHERE RN=1) ls ON ls.MEMBER_ID=m.MEMBER_ID
    """

    def __init__(self, settings):
        self.settings = settings
        self.tz = ZoneInfo(settings["ORACLE_DB_TIMEZONE"])
        self._pool = None
        self._lock = threading.RLock()
        self._cache = {}

    def _get_pool(self):
        with self._lock:
            if self._pool is None:
                import oracledb
                from .oracle_client import pool_options
                opts = dict(user=self.settings["ORACLE_USER"], password=self.settings["ORACLE_PASSWORD"],
                            min=0, max=3, increment=1,
                            getmode=oracledb.POOL_GETMODE_TIMEDWAIT, wait_timeout=3000,
                            timeout=60, tcp_connect_timeout=5)
                opts.update(pool_options(self.settings, oracledb))
                self._pool = oracledb.create_pool(**opts)
            return self._pool

    @contextmanager
    def read(self):
        try:
            with self._get_pool().acquire() as conn:
                conn.call_timeout = 5000
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                try:
                    yield conn
                finally:
                    conn.rollback()
        except Exception as exc:
            raise DataUnavailable("数据库暂时不可用，请检查连接、只读权限及所需对象。") from exc

    def query(self, conn, sql, binds=None):
        with conn.cursor() as cur:
            cur.arraysize = 100
            cur.execute(sql, binds or {})
            columns = [c[0].lower() for c in cur.description]
            result = []
            for row in cur:
                item = {}
                for key, value in zip(columns, row):
                    if isinstance(value, datetime):
                        value = iso(value.replace(tzinfo=self.tz) if value.tzinfo is None else value)
                    elif hasattr(value, "read"):
                        value = value.read()
                    item[key] = value
                result.append(item)
            return result

    def cached(self, key, callback):
        # Bound memory and coalesce concurrent reads; never fall back to demo on errors.
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < 10:
                return copy.deepcopy(hit[1])
            result = callback()
            if len(self._cache) >= 128:
                self._cache.clear()
            self._cache[key] = (time.monotonic(), result)
            return copy.deepcopy(result)

    def groups(self):
        def fetch():
            with self.read() as conn:
                return [r["name"] for r in self.query(conn, "SELECT NAME FROM ADMIN.GROUPS ORDER BY NAME")]
        return self.cached("groups", fetch)

    def members(self, search="", group="", enabled="", page=1, page_size=12):
        def fetch():
            clauses, binds = [], {}
            if search:
                clauses.append("(INSTR(LOWER(m.MEMBER_ID), :search)>0 OR INSTR(LOWER(m.NAME_EN), :search)>0 "
                               "OR INSTR(LOWER(m.NAME_JP), :search)>0 OR INSTR(m.ROOM_ID, :search)>0)")
                binds["search"] = search.lower()
            if group:
                clauses.append("g.NAME=:group_name")
                binds["group_name"] = group
            if enabled:
                clauses.append("m.ENABLED=:enabled")
                binds["enabled"] = int(enabled)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            with self.read() as conn:
                total = self.query(conn, "SELECT COUNT(*) AS TOTAL FROM ADMIN.MEMBERS m JOIN ADMIN.GROUPS g ON g.ID=m.GROUP_ID" + where, binds)[0]["total"]
                items = self.query(conn, self.member_select + where +
                    " ORDER BY CASE WHEN ls.IS_LIVE=1 THEN 0 ELSE 1 END, m.ID ASC OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY",
                    {**binds, "offset":(page-1)*page_size, "limit":page_size})
                return dict(total=total, items=items)
        return self.cached(("members", search, group, enabled, page, page_size), fetch)

    def _live(self, conn):
        return self.query(conn, self.member_select + " WHERE ls.IS_LIVE=1 ORDER BY ls.STARTED_AT DESC NULLS LAST, m.ID")

    def live(self):
        def fetch():
            with self.read() as conn:
                return self._live(conn)
        return self.cached("live", fetch)

    def _instances(self, conn):
        return self.query(conn, "SELECT INSTANCE_ID, INSTANCE_TYPE, DISPLAY_NAME, MAX_CAPACITY, STATUS, "
            "CONFIG_VERSION, CURRENT_LOAD, AVAILABLE_CAPACITY, LOAD_PERCENT, LAST_HEARTBEAT, UPDATED_AT "
            "FROM ADMIN.V_INSTANCE_LOAD ORDER BY INSTANCE_TYPE, INSTANCE_ID")

    def instances(self):
        def fetch():
            with self.read() as conn:
                return self._instances(conn)
        return self.cached("instances", fetch)

    def overview(self):
        def fetch():
            with self.read() as conn:
                counts = self.query(conn, "SELECT COUNT(*) AS TOTAL_MEMBERS, COUNT(CASE WHEN ENABLED=1 THEN 1 END) AS ENABLED_MEMBERS FROM ADMIN.MEMBERS")[0]
                return dict(**counts, live=self._live(conn), instances=self._instances(conn), observed_at=iso(now()))
        return self.cached("overview", fetch)

    def detail(self, member_id):
        def fetch():
            with self.read() as conn:
                rows = self.query(conn, self.member_select + " WHERE m.MEMBER_ID=:member_id", {"member_id":member_id})
                if not rows:
                    return None
                result = rows[0]
                configs = self.query(conn, "SELECT TITLE_TEMPLATE, DESCRIPTION_TEMPLATE, CATEGORY_ID, PRIVACY_STATUS, "
                    "PLAYLIST_ID, USE_PRIMARY_ACCOUNT FROM ADMIN.YOUTUBE_CONFIGS WHERE MEMBER_ID=:id", {"id":result["id"]})
                result["youtube"] = configs[0] if configs else None
                result["tags"] = [r["tag"] for r in self.query(conn,
                    "SELECT TAG FROM ADMIN.YOUTUBE_TAGS WHERE MEMBER_ID=:id ORDER BY SORT_ORDER, ID", {"id":result["id"]})]
                result["assignments"] = self.query(conn, "SELECT INSTANCE_ID, INSTANCE_TYPE, ENABLED, ASSIGNED_BY, ASSIGNED_AT "
                    "FROM ADMIN.MEMBER_INSTANCES WHERE MEMBER_ID=:id ORDER BY INSTANCE_TYPE, INSTANCE_ID", {"id":result["id"]})
                result["history"] = self.query(conn, "SELECT STARTED_AT, ENDED_AT, DURATION_MINUTES FROM ADMIN.SHOWROOM_LIVE_HISTORY "
                    "WHERE MEMBER_ID=:member_id ORDER BY STARTED_AT DESC, ID DESC FETCH FIRST 20 ROWS ONLY", {"member_id":member_id})
                return result
        return self.cached(("detail", member_id), fetch)
