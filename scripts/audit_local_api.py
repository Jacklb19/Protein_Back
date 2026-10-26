"""Sustained loopback HTTP audit with simulated identity/storage and no outbound network."""
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

import httpx
import uvicorn

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    ('health', 'GET', '/v1/health', None),
    ('status-connect', 'POST', '/proteinforge.account.v1.AccountService/GetStatus', {}),
    ('identity', 'GET', '/v1/me', None),
    ('projects-rest', 'GET', '/v1/projects', None),
    ('projects-connect', 'POST', '/proteinforge.project.v1.ProjectService/ListProjects', {}),
)


def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 3) if ordered else None


async def run(args):
    provider_calls = Counter()
    def provider(request):
        # MockTransport never opens a socket, even if the app unexpectedly asks for another URL.
        if request.url.host != 'audit.supabase.invalid':
            raise AssertionError('Unexpected provider destination in local audit')
        provider_calls[request.url.path] += 1
        if request.url.path == '/auth/v1/user':
            return httpx.Response(200, json={'id': 'local-audit-only', 'email': ''})
        if request.url.path == '/rest/v1/rpc/pf_list_projects':
            return httpx.Response(200, json=[])
        raise AssertionError('Unexpected data operation in local audit')

    settings = Settings(supabase_url='https://audit.supabase.invalid', supabase_anon_key='audit-public-only')
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
                            headers={'Authorization': 'Bearer local-audit-only', 'Connect-Protocol-Version': '1'})
                        record.update(status=response.status_code, correlated=bool(response.headers.get('X-Request-Id')),
                                      noStore=response.headers.get('Cache-Control') == 'no-store')
                        record['ok'] = response.status_code == 200 and record['correlated'] and record['noStore']
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

    latencies = [item['latencyMs'] for item in observations]
    summary = {
        'recordedAt': datetime.now(timezone.utc).isoformat(), 'scope': 'LOCAL HTTP ONLY; simulated identity and storage; no external load',
        'python': platform.python_version(), 'platform': platform.platform(), 'logicalCpus': os.cpu_count(),
        'requestedRatePerMinute': args.rate, 'requestedSeconds': args.seconds,
        'actualSeconds': round(duration, 3), 'requests': len(observations),
        'effectiveRatePerMinute': round(len(observations) * 60 / duration, 3),
        'errors': sum(not item['ok'] for item in observations),
        'latencyP50Ms': percentile(latencies, .5), 'latencyP95Ms': percentile(latencies, .95), 'latencyMaxMs': max(latencies),
        'maxSchedulingLagMs': max(item['lagMs'] for item in observations),
        'processCpuSeconds': round(cpu, 3), 'processCpuScope': 'server and load client in this process; excludes other concurrent work',
        'simulatedProviderCalls': dict(provider_calls),
        'cases': {name: {'requests': sum(item['case'] == name for item in observations),
                         'p95Ms': percentile([item['latencyMs'] for item in observations if item['case'] == name], .95)}
                  for name, *_ in CASES},
    }
    destination = ROOT / 'artifacts' / 'quality' / 'local-api-load.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({'summary': summary, 'observations': observations}, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary))
    if summary['errors']:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300, choices=range(1, 301), metavar='1..300')
    parser.add_argument('--rate', type=int, default=100, choices=range(1, 101), metavar='1..100')
    parser.add_argument('--port', type=int, default=5335, choices=range(5335, 5340), metavar='5335..5339')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
