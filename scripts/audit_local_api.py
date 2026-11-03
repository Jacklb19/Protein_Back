"""Sustained loopback HTTP audit with no outbound network.

Storage is simulated by default. `--storage postgres` sends the listing RPC to a disposable local cluster
(see scripts/audit-local-api-postgres.ps1) under the `authenticated` role, so RLS and the SQL run for real.
Identity stays simulated in both modes: Supabase Auth and PostgREST themselves are not part of this audit.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import socket
from time import monotonic, process_time
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import uvicorn

from app.config import Settings
from app.main import create_app
from app.project_config import PROJECT_LIST_RPC
from app.projects import DEFAULT_PAGE_SIZE

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_HOST = 'audit.supabase.invalid'
AUDIT_TOKEN = 'local-audit-only'
AUDIT_PORTS = (5296, *range(5335, 5340))
DEFAULT_PORT = 5335
CASES = (
    ('health', 'GET', '/v1/health', None),
    ('status-connect', 'POST', '/proteinforge.account.v1.AccountService/GetStatus', {}),
    ('identity', 'GET', '/v1/me', None),
    ('projects-rest', 'GET', '/v1/projects', None),
    ('projects-connect', 'POST', '/proteinforge.project.v1.ProjectService/ListProjects', {}),
)
# Listing field per protocol; the item count proves RLS kept only the caller's rows.
LISTING_FIELDS = {'projects-rest': 'items', 'projects-connect': 'projects'}

# Local database lane: only a loopback cluster with this exact database name is accepted.
DATABASE_URL_VARIABLE, PSQL_VARIABLE = 'PF_AUDIT_DATABASE_URL', 'PF_PSQL'
DATABASE_NAME = 'pf_load_audit'
LOOPBACK_HOSTS = ('127.0.0.1', 'localhost')
SEED_SEQUENCE = 'ACDEFGHIKLMNPQRSTVWY'
SEED_SEQUENCE_LENGTH = 300
SEED_WORKSPACE = {'schemaVersion': 1, 'profileWindow': 9, 'descriptors': None,
                  'alignment': {'first': 'ACD', 'second': 'ACD', 'matrix': 'BLOSUM62', 'mode': 'global', 'result': None}}
DEFAULT_OWNED_PROJECTS, DEFAULT_FOREIGN_USERS, DEFAULT_FOREIGN_PROJECTS = 12, 50, 20
END_MARKER = '__pf_audit_end__'


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3) if ordered else None


def literal(value):
    """A standard-conforming SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


class LocalDatabase:
    """One psql session on a disposable loopback cluster, used like PostgREST: a transaction per call,
    `set local role authenticated` and the caller's subject claim, so RLS applies to every query."""

    def __init__(self, psql, url):
        parsed = urlparse(url)
        if parsed.hostname not in LOOPBACK_HOSTS or parsed.path != f'/{DATABASE_NAME}':
            raise RuntimeError(f'Refusing a database other than loopback /{DATABASE_NAME}')
        self.psql, self.url, self.lock, self.process = psql, url, asyncio.Lock(), None

    async def __aenter__(self):
        self.process = await asyncio.create_subprocess_exec(
            self.psql, self.url, '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=0', '-v', 'VERBOSITY=sqlstate',
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, 'PGCLIENTENCODING': 'UTF8'})
        return self

    async def __aexit__(self, *_):
        self.process.stdin.close()
        await asyncio.wait_for(self.process.wait(), timeout=5)

    async def execute(self, sql):
        """Rows printed by `sql`, or the SQLSTATE of its first error."""
        async with self.lock:
            self.process.stdin.write(f'{sql}\n\\echo {END_MARKER}\n'.encode())
            await self.process.stdin.drain()
            lines = []
            while (line := (await self.process.stdout.readline()).decode().rstrip('\r\n')) != END_MARKER:
                if not line and self.process.stdout.at_eof():
                    raise RuntimeError('psql session ended')
                lines.append(line)
        errors = [line.split('ERROR:', 1)[1].strip() for line in lines if 'ERROR:' in line]
        return (None, errors[0]) if errors else (lines, None)

    async def as_caller(self, subject, sql):
        return await self.execute(f'begin; set local role authenticated; set local request.jwt.claim.sub = {literal(subject)};'
                                  f' {sql}; commit;')

    async def seed(self, owner, owned, foreign_users, foreign_projects):
        sequence = (SEED_SEQUENCE * math.ceil(SEED_SEQUENCE_LENGTH / len(SEED_SEQUENCE)))[:SEED_SEQUENCE_LENGTH]
        rows, error = await self.execute(f"""
            insert into auth.users(id) values ({literal(owner)});
            insert into auth.users(id) select gen_random_uuid() from generate_series(1, {int(foreign_users)});
            do $seed$ declare member uuid; total integer; begin
              for member in select id from auth.users loop
                perform set_config('request.jwt.claim.sub', member::text, true);
                total := case when member = {literal(owner)}::uuid then {int(owned)} else {int(foreign_projects)} end;
                for number in 1..total loop
                  perform public.pf_save_project(null, 'Load audit ' || number, {literal(sequence)},
                                                 {literal(json.dumps(SEED_WORKSPACE))}::jsonb, null);
                end loop;
              end loop;
            end $seed$;
            analyze;
            select count(*) from public.projects;""")
        if error:
            raise RuntimeError(f'Seeding failed with SQLSTATE {error}')
        return int(rows[-1])


async def run(args):
    provider_calls, storage_ms = Counter(), []
    postgres = args.storage == 'postgres'
    owner = str(uuid4()) if postgres else 'local-audit-only'
    database = LocalDatabase(os.environ.get(PSQL_VARIABLE, 'psql'), os.environ.get(DATABASE_URL_VARIABLE, '')) if postgres else None

    async def provider(request):
        # MockTransport never opens a socket, even if the app unexpectedly asks for another URL.
        if request.url.host != PROVIDER_HOST:
            raise AssertionError('Unexpected provider destination in local audit')
        provider_calls[request.url.path] += 1
        if request.url.path == '/auth/v1/user':
            return httpx.Response(200, json={'id': owner, 'email': ''})
        if request.url.path == f'/rest/v1/{PROJECT_LIST_RPC}':
            if not postgres:
                return httpx.Response(200, json=[])
            body = json.loads(request.content)
            began = monotonic()
            rows, error = await database.as_caller(owner, 'select coalesce(jsonb_agg(p), \'[]\'::jsonb) from public.pf_list_projects('
                f"{int(body['p_limit'])}, {int(body['p_offset'])}, {literal(body['p_query'])}, {literal(body['p_order'])},"
                f" {int(body['p_prefix_length'])}) p")
            storage_ms.append(round((monotonic() - began) * 1000, 3))
            return httpx.Response(400, json={'code': error, 'message': ''}) if error else httpx.Response(200, content=rows[-1])
        raise AssertionError('Unexpected data operation in local audit')

    settings = Settings(supabase_url=f'https://{PROVIDER_HOST}', supabase_anon_key='audit-public-only')
    seeded = None
    if postgres:
        await database.__aenter__()
    try:
        if postgres:
            seeded = await database.seed(owner, args.owned_projects, args.foreign_users, args.foreign_projects)
            # Policies alone, without the listing's own owner filter, must hide every foreign row.
            rows, error = await database.as_caller(owner, 'select count(*) from public.projects')
            if error or int(rows[-1]) != args.owned_projects:
                raise RuntimeError('RLS did not limit the audit caller to its own projects')
        expected_items = args.owned_projects if postgres else 0
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as providers:
            application = create_app(settings, providers)
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(('127.0.0.1', args.port))
            listener.setblocking(False)
            server = uvicorn.Server(uvicorn.Config(application, host='127.0.0.1', port=args.port,
                                                   access_log=False, log_level='warning', lifespan='on'))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            observations = []
            try:
                for _ in range(500):
                    if server.started:
                        break
                    if task.done():
                        await task
                        raise RuntimeError('Local server did not start')
                    await asyncio.sleep(.01)
                if not server.started:
                    raise RuntimeError('Local server startup timed out')
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{args.port}', timeout=5,
                                              trust_env=False, follow_redirects=False) as client:
                    warmup = await client.get('/v1/health')
                    warmup.raise_for_status()
                    storage_ms.clear()
                    count = math.ceil(args.seconds * args.rate / 60)
                    start, cpu_start = monotonic(), process_time()
                    for number in range(count):
                        scheduled = start + number * 60 / args.rate
                        await asyncio.sleep(max(0, scheduled - monotonic()))
                        name, method, path, body = CASES[number % len(CASES)]
                        began = monotonic()
                        record = {'case': name, 'scheduledSeconds': round(scheduled - start, 3),
                                  'lagMs': round(max(0, began - scheduled) * 1000, 3)}
                        try:
                            response = await client.request(method, path, json=body,
                                headers={'Authorization': f'Bearer {AUDIT_TOKEN}', 'Connect-Protocol-Version': '1'})
                            record.update(status=response.status_code, correlated=bool(response.headers.get('X-Request-Id')),
                                          noStore=response.headers.get('Cache-Control') == 'no-store')
                            record['ok'] = response.status_code == 200 and record['correlated'] and record['noStore']
                            if name in LISTING_FIELDS and response.status_code == 200:
                                record['items'] = len(response.json().get(LISTING_FIELDS[name], []))
                                record['ok'] = record['ok'] and record['items'] == expected_items
                        except httpx.HTTPError as error:
                            record.update(ok=False, error=type(error).__name__)
                        record['latencyMs'] = round((monotonic() - began) * 1000, 3)
                        observations.append(record)
                    await asyncio.sleep(max(0, start + args.seconds - monotonic()))
                    duration, cpu = monotonic() - start, process_time() - cpu_start
            finally:
                server.should_exit = True
                try:
                    await asyncio.wait_for(task, timeout=5)
                finally:
                    listener.close()
    finally:
        if postgres:
            await database.__aexit__()

    latencies = [item['latencyMs'] for item in observations]
    scope = ('LOCAL HTTP + local PostgreSQL with migrations and RLS; simulated identity, PostgREST emulated by psql; no external load'
             if postgres else 'LOCAL HTTP ONLY; simulated identity and storage; no external load')
    summary = {
        'recordedAt': datetime.now(timezone.utc).isoformat(), 'scope': scope, 'storage': args.storage,
        'python': platform.python_version(), 'platform': platform.platform(), 'logicalCpus': os.cpu_count(),
        'requestedRatePerMinute': args.rate, 'requestedSeconds': args.seconds,
        'actualSeconds': round(duration, 3), 'requests': len(observations),
        'effectiveRatePerMinute': round(len(observations) * 60 / duration, 3),
        'errors': sum(not item['ok'] for item in observations),
        'latencyP50Ms': percentile(latencies, .5), 'latencyP95Ms': percentile(latencies, .95),
        'latencyP99Ms': percentile(latencies, .99), 'latencyMaxMs': max(latencies),
        'maxSchedulingLagMs': max(item['lagMs'] for item in observations),
        'processCpuSeconds': round(cpu, 3),
        'processCpuScope': 'server and load client in this process; excludes psql, PostgreSQL and other concurrent work',
        'simulatedProviderCalls': dict(provider_calls),
        'cases': {name: {'requests': sum(item['case'] == name for item in observations),
                         'p50Ms': percentile([item['latencyMs'] for item in observations if item['case'] == name], .5),
                         'p95Ms': percentile([item['latencyMs'] for item in observations if item['case'] == name], .95)}
                  for name, *_ in CASES},
    }
    if postgres:
        summary['database'] = {
            'seededProjects': seeded, 'ownedProjects': args.owned_projects, 'rlsVisibleProjects': args.owned_projects,
            'foreignUsers': args.foreign_users, 'foreignProjectsPerUser': args.foreign_projects,
            'expectedListingItems': expected_items, 'listingCalls': len(storage_ms),
            'roundTripP50Ms': percentile(storage_ms, .5), 'roundTripP95Ms': percentile(storage_ms, .95),
            'roundTripP99Ms': percentile(storage_ms, .99), 'roundTripMaxMs': max(storage_ms, default=None),
        }
    suffix = '-postgres' if postgres else ''
    destination = ROOT / 'artifacts' / 'quality' / f'local-api-load{suffix}.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({'summary': summary, 'observations': observations}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary))
    if summary['errors']:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300, choices=range(1, 301), metavar='1..300')
    parser.add_argument('--rate', type=int, default=100, choices=range(1, 101), metavar='1..100')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, choices=AUDIT_PORTS, metavar='|'.join(map(str, AUDIT_PORTS)))
    parser.add_argument('--storage', default='simulated', choices=('simulated', 'postgres'))
    # Below the page size, so a leaked foreign row would change the item count.
    parser.add_argument('--owned-projects', type=int, default=DEFAULT_OWNED_PROJECTS, choices=range(1, DEFAULT_PAGE_SIZE),
                        metavar=f'1..{DEFAULT_PAGE_SIZE - 1}')
    parser.add_argument('--foreign-users', type=int, default=DEFAULT_FOREIGN_USERS, choices=range(0, 1001), metavar='0..1000')
    parser.add_argument('--foreign-projects', type=int, default=DEFAULT_FOREIGN_PROJECTS, choices=range(0, 101), metavar='0..100')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
