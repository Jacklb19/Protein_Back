"""Bounded public reference transport, independent of identity and provider availability."""
import unittest
from unittest.mock import patch
import httpx
from fastapi import HTTPException
from app.config import Settings
from app.context import ServiceContext
from app.rate_limit import FixedWindowLimiter
from app.uniprot import retrieve
from test_sifts import RawStream

class UniProtAcquisitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_fetch_strips_client_auth_and_closes_a_bounded_stream(self):
        stream = RawStream([b'{"public":true}'])
        def handler(request):
            for header in ('Authorization', 'apikey', 'Cookie'):
                self.assertNotIn(header, request.headers)
            self.assertEqual(request.headers['Accept-Encoding'], 'identity')
            return httpx.Response(200, stream=stream)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=('private', 'secret'),
                                    headers={'apikey': 'private'}, cookies={'session': 'private'}) as client:
            context = ServiceContext(Settings(), client, FixedWindowLimiter(20), FixedWindowLimiter(6))
            self.assertEqual(await retrieve(context, 'P01308.json'), {'public': True})
        self.assertTrue(stream.closed)

    async def test_reference_download_rejects_oversize_redirect_and_encoding_before_parsing(self):
        cases = [(200, {}, [b'x' * 65_536], 'uniprot-provider-error'),
                 (200, {'Content-Length': '999'}, [b'x'], 'uniprot-provider-error'),
                 (302, {'Location': 'https://private.invalid/'}, [b'private-provider'], 'uniprot-provider-error'),
                 (200, {'Content-Encoding': 'gzip'}, [b'private-provider'], 'uniprot-invalid-response')]
        for status, headers, chunks, code in cases:
            stream = RawStream(chunks)
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, headers=headers, stream=stream)),
                                        follow_redirects=True) as client:
                context = ServiceContext(Settings(), client, FixedWindowLimiter(20), FixedWindowLimiter(6))
                with patch('app.uniprot.MAX_UNIPROT_BYTES', 100), self.assertRaises(HTTPException) as caught:
                    await retrieve(context, 'P01308.json')
                self.assertEqual(caught.exception.detail, code)
                self.assertTrue(stream.closed)
                self.assertLessEqual(stream.read, 1)
