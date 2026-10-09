"""Provider catalog browser using the private management API, without a Forge key."""
import asyncio
import threading
import time
from .forge import ForgeError, author_of, listing_id, recent_of, title_of
from .safety import clean


class ManagedForgeClient:
    def __init__(self, fleet):
        self.fleet = fleet
        self.stop = threading.Event()
        self.quota = ''
        self.data = {}
        self.fetched = 0

    async def snapshot(self, fresh=False):
        if fresh or time.monotonic() - self.fetched > 60 or not self.data:
            try:
                data = await asyncio.to_thread(self.fleet.request, '/api/forge')
                if not isinstance(data, dict) or data.get('mode') != 'managed-read-only':
                    raise ValueError('catalog endpoint is unavailable')
                self.data = clean(data)
                self.fetched = time.monotonic()
            except Exception:
                if not self.data:
                    raise ForgeError(503, 'The private controller catalog is unavailable.') from None
        return self.data

    async def listings(self, sort='trending', window='7d', query='', page_size=50, more=None, fresh=False):
        data = await self.snapshot(fresh)
        rows = [x for x in data.get('items', []) if query.casefold() in
                (title_of(x) + ' ' + author_of(x) + ' ' + ' '.join(x.get('tags', []))).casefold()]
        if sort == 'unrated':
            rows = [x for x in rows if not x.get('rating_count')]
        if sort == 'rising' and window != '7d':
            raise ForgeError(502, 'The shared rising ranking is collected for 7d; choose that window.')
        rank = {
            'latest': lambda x: x.get('created_at') or '',
            'updated': lambda x: x.get('updated_at') or '',
            'downloads': lambda x: x.get('download_count') or 0,
            'rated': lambda x: (x.get('rating_average') or 0, x.get('rating_count') or 0),
            'unrated': lambda x: x.get('created_at') or '',
            'overlooked': lambda x: -(x.get('download_count') or 0),
            'trending': lambda x: recent_of(x, window) or 0,
            'rising': lambda x: -(x.get('rising_rank') or 1000000),
        }.get(sort)
        if rank:
            rows.sort(key=rank, reverse=True)
        page = max(1, int((more or {}).get('page', 1)))
        offset = (page - 1) * page_size
        age = max(0, time.time() - (data.get('at') or 0))
        return rows[offset:offset + page_size], ({'page': page + 1} if offset + page_size < len(rows) else None), {
            '_stale': int(age) if age > 900 else 0, 'at': data.get('at')}

    async def listing(self, lid, background=False):
        data = await self.snapshot()
        row = next((x for x in data.get('items', []) if listing_id(x) == lid), None)
        if row is None:
            raise ForgeError(404)
        return row

    async def manifest(self, *args, **kwargs):
        raise ForgeError(403, 'Fleet installs use the managed controller pipeline.')
