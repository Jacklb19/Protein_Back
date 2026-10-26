"""Public source reconciliation and bounded transport, without accounts or network."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

import httpx

from app.sifts import SIFTS_NAMESPACE, SiftsError, SourceLimits, fetch_mapping, normalise_mapping, source_urls

FIXTURES = Path(__file__).parent / "fixtures" / "sifts"
XML = (FIXTURES / "1trz.xml").read_bytes()
LISTING = json.loads((FIXTURES / "1trz.json").read_text(encoding="utf-8"))
STAMP = datetime(2026, 10, 25, tzinfo=timezone.utc)


def synthetic_xml(author="1", insertion="", accession="P01308", position="90", detail=""):
    return f'''<entry xmlns="{SIFTS_NAMESPACE}" dbAccessionId="1trz" date="2026-10-18">
      <entity entityId="A"><segment segId="synthetic"><listResidue>
        <residue dbSource="PDBe" dbCoordSys="PDBe" dbResNum="1">
          <crossRefDb dbSource="PDB" dbCoordSys="PDBresnum" dbAccessionId="1trz"
            dbResNum="{author}{insertion}" dbResName="GLY" dbChainId="A"/>
          <crossRefDb dbSource="UniProt" dbCoordSys="UniProt" dbAccessionId="{accession}"
            dbResNum="{position}" dbResName="G"/>{detail}
        </residue>
      </listResidue></segment></entity>
    </entry>'''.encode()


def synthetic_listing(author=1, insertion=""):
    return {"1trz": {"molecules": [{"entity_id": 1, "chains": [{
        "chain_id": "A", "struct_asym_id": "internal-A", "residues": [{
            "residue_number": 1, "residue_name": "GLY", "author_residue_number": author,
            "author_insertion_code": insertion, "observed_ratio": 0.0,
        }],
    }]}]}}


class MappingTests(unittest.TestCase):
    def assert_invalid(self, xml=XML, listing=LISTING, pdb_id="1TRZ", **kwargs):
        with self.assertRaises(SiftsError) as caught:
            normalise_mapping(xml, listing, pdb_id, **kwargs)
        return caught.exception.code

    def test_public_excerpt_keeps_eight_explicit_rows_and_all_chain_copies(self):
        mapping = normalise_mapping(XML, LISTING, "1TRZ", retrieved_at=STAMP)
        self.assertEqual(len(mapping.rows), 8)
        glycine = [row for row in mapping.rows if row.position == 90]
        self.assertEqual([(row.auth_asym_id, row.label_seq_id) for row in glycine], [("A", 1), ("C", 1)])
        self.assertEqual({row.position for row in mapping.rows}, {25, 54, 90, 110})
        self.assertNotIn(91, [row.position for row in mapping.rows])
        self.assertEqual(mapping.provenance.release_date, "2026-10-18")
        self.assertEqual(mapping.provenance.retrieved_at, STAMP.isoformat())
        payload = mapping.model_dump(mode="json", by_alias=True)
        self.assertEqual(payload["rows"][0]["authSeqId"], "1")
        self.assertEqual(payload["pdbId"], "1TRZ")
        self.assertNotIn("modelNum", payload["rows"][0])

    def test_author_text_insertion_and_isoform_are_not_coerced_or_split(self):
        for author, insertion in [("-2", "A"), ("custom", "B"), ("01", "")]:
            row = normalise_mapping(synthetic_xml(author, insertion, "P01308-2"),
                                    synthetic_listing(author, insertion), "1TRZ").rows[0]
            self.assertEqual((row.auth_seq_id, row.insertion_code, row.label_asym_id),
                             (author, insertion, "internal-A"))
            self.assertEqual(row.accession, "P01308-2")

    def test_missing_coordinate_author_and_observation_annotation_stay_explicit(self):
        detail = '<residueDetail dbSource="PDBe" property="Annotation">Not_Observed</residueDetail>'
        row = normalise_mapping(synthetic_xml("null", detail=detail), synthetic_listing(None), "1TRZ").rows[0]
        self.assertIsNone(row.auth_seq_id)
        self.assertTrue(row.not_observed)
        # PDBe observed_ratio alone is not a coordinate observation in a particular model.
        self.assertFalse(normalise_mapping(synthetic_xml(), synthetic_listing(), "1TRZ").rows[0].not_observed)

    def test_rejects_conflicting_or_ambiguous_listing_identity(self):
        for change in ("duplicate", "name", "author", "chain", "bool"):
            listing = synthetic_listing()
            chain = listing["1trz"]["molecules"][0]["chains"][0]
            if change == "duplicate":
                chain["residues"].append(deepcopy(chain["residues"][0]))
            elif change == "name":
                chain["residues"][0]["residue_name"] = "ALA"
            elif change == "author":
                chain["residues"][0]["author_residue_number"] = 9
            elif change == "chain":
                chain["chain_id"] = "other"
            else:
                chain["residues"][0]["residue_number"] = True
            with self.subTest(change=change):
                self.assertEqual(self.assert_invalid(synthetic_xml(), listing), "sifts-invalid-response")

    def test_rejects_wrong_entry_layout_and_invalid_numbers(self):
        for xml in [synthetic_xml().replace(b'"1trz"', b'"2abc"', 1),
                    synthetic_xml().replace(b"listResidue", b"unsupported"),
                    synthetic_xml(position="-1"), synthetic_xml(position="1.2"),
                    synthetic_xml(position="0"), synthetic_xml().replace(b"2026-10-18", b"2026-02-30"),
                    b"not XML"]:
            self.assertEqual(self.assert_invalid(xml, synthetic_listing()), "sifts-invalid-response")

    def test_dtd_is_rejected_before_entity_expansion_including_utf16(self):
        document = '<!DOCTYPE entry [<!ENTITY payload "private-file-or-bomb">]>' + synthetic_xml().decode()
        for encoding in ("utf-8", "utf-16"):
            self.assertEqual(self.assert_invalid(document.encode(encoding), synthetic_listing()), "sifts-invalid-response")

    def test_foreign_namespace_and_conflicting_duplicate_rows_are_not_absence(self):
        root = ET.fromstring(synthetic_xml())
        ns = f"{{{SIFTS_NAMESPACE}}}"
        refs = list(root.iter(ns + "crossRefDb"))
        refs[-1].tag = "{https://untrusted.invalid/}crossRefDb"
        self.assertEqual(self.assert_invalid(ET.tostring(root), synthetic_listing()), "sifts-invalid-response")
        root = ET.fromstring(synthetic_xml())
        group = next(root.iter(ns + "listResidue"))
        duplicate = deepcopy(group[0])
        detail = ET.SubElement(duplicate, ns + "residueDetail", {"dbSource": "PDBe", "property": "Annotation"})
        detail.text = "Not_Observed"
        group.append(duplicate)
        self.assertEqual(self.assert_invalid(ET.tostring(root), synthetic_listing()), "sifts-invalid-response")

    def test_bounds_tree_depth_element_count_and_aggregate_residues(self):
        for limits in (SourceLimits(xml_depth=2), SourceLimits(xml_elements=2), SourceLimits(residues=2), SourceLimits(xml_bytes=10)):
            with self.subTest(limits=limits):
                self.assertEqual(self.assert_invalid(limits=limits), "sifts-source-too-large")

    def test_empty_mapping_is_valid_only_when_no_uniprot_rows_exist(self):
        xml = f'<entry xmlns="{SIFTS_NAMESPACE}" dbAccessionId="1trz" date="2026-10-18"/>'.encode()
        result = normalise_mapping(xml, {"1trz": {"molecules": []}}, "1TRZ")
        self.assertEqual(result.rows, ())
        self.assert_invalid(XML, {"1trz": {"molecules": []}})
        self.assertEqual(self.assert_invalid(pdb_id="../private"), "sifts-invalid-request")
        self.assertEqual(self.assert_invalid(retrieved_at=datetime(2026, 10, 25)), "sifts-invalid-request")

    def test_cpu_normalisation_checks_the_same_deadline_as_downloads(self):
        with patch("app.sifts.monotonic", return_value=21):
            self.assertEqual(self.assert_invalid(_deadline=20), "sifts-timeout")


class RawStream(httpx.AsyncByteStream):
    def __init__(self, chunks, delay=0):
        self.chunks, self.delay, self.closed, self.read = chunks, delay, False, 0

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.read += 1
            yield chunk

    async def aclose(self):
        self.closed = True


class AcquisitionTests(unittest.IsolatedAsyncioTestCase):
    async def acquire(self, handler, limits=SourceLimits()):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_mapping(client, "1TRZ", limits=limits)

    async def assert_failure(self, handler, expected, **kwargs):
        with self.assertRaises(SiftsError) as caught:
            await self.acquire(handler, **kwargs)
        self.assertEqual(caught.exception.code, expected)
        self.assertNotIn("provider-private", str(caught.exception))

    async def test_never_forwards_client_defaults_credentials_cookies_or_user_queries(self):
        requests, streams = [], []
        def handler(request):
            requests.append(request)
            for header in ("Authorization", "apikey", "Cookie"):
                self.assertNotIn(header, request.headers)
            self.assertEqual(request.headers["Accept-Encoding"], "identity")
            body = gzip.compress(XML) if request.url.host == "ftp.ebi.ac.uk" else json.dumps(LISTING).encode()
            stream = RawStream([body[:17], body[17:]])
            streams.append(stream)
            return httpx.Response(200, stream=stream, headers={"Set-Cookie": "session=provider-private"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True,
                                    auth=("private", "secret"), headers={"Authorization": "Bearer private", "apikey": "private"},
                                    cookies={"session": "private"}) as client:
            mapping = await fetch_mapping(client, "1TRZ")
        self.assertEqual(len(mapping.rows), 8)
        self.assertEqual([str(request.url) for request in requests], list(source_urls("1TRZ")))
        self.assertTrue(all(stream.closed for stream in streams))

    async def test_redirect_and_provider_failures_do_not_expose_bodies(self):
        for status, code in [(302, "sifts-provider-error"), (404, "sifts-not-found"),
                             (429, "sifts-provider-rate-limit"), (500, "sifts-provider-error")]:
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(status, headers={"Location": "https://private.invalid/"},
                                      stream=RawStream([b"provider-private"]))
            await self.assert_failure(handler, code)
            self.assertEqual(len(requests), 1)

    async def test_declared_size_and_actual_stream_are_bounded_and_closed(self):
        for declared in (True, False):
            stream = RawStream([b"a" * 65_536] * 3)
            headers = {"Content-Length": "10000000"} if declared else {}
            await self.assert_failure(lambda _: httpx.Response(200, stream=stream, headers=headers),
                                      "sifts-source-too-large", limits=SourceLimits(compressed_bytes=100))
            self.assertTrue(stream.closed)
            self.assertEqual(stream.read, 0 if declared else 1)

    async def test_gzip_expansion_crc_truncation_and_concatenated_members(self):
        bad_crc = bytearray(gzip.compress(XML))
        bad_crc[-8] ^= 1
        for body, code in [(gzip.compress(b"x" * 10_000), "sifts-source-too-large"),
                           (gzip.compress(XML)[:-3], "sifts-invalid-response"),
                           (bytes(bad_crc), "sifts-invalid-response"),
                           (gzip.compress(XML) + gzip.compress(XML), "sifts-invalid-response"),
                           (b"not gzip", "sifts-invalid-response")]:
            await self.assert_failure(lambda _: httpx.Response(200, stream=RawStream([body])), code,
                                      limits=SourceLimits(xml_bytes=1000 if code.endswith("too-large") else 10_000))

    async def test_json_duplicates_nonfinite_and_broken_utf8_are_refused(self):
        for body in (b'{"1trz":{},"1trz":{}}', b'{"value":NaN}', b'\xff', b'[]'):
            def handler(request):
                payload = gzip.compress(XML) if request.url.host == "ftp.ebi.ac.uk" else body
                return httpx.Response(200, stream=RawStream([payload]))
            await self.assert_failure(handler, "sifts-invalid-response")

    async def test_total_timeout_and_external_cancellation_close_the_stream(self):
        stream = RawStream([gzip.compress(XML)], delay=.1)
        await self.assert_failure(lambda _: httpx.Response(200, stream=stream), "sifts-timeout",
                                  limits=SourceLimits(timeout_seconds=.01))
        self.assertTrue(stream.closed)
        stream = RawStream([gzip.compress(XML)], delay=10)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
            task = asyncio.create_task(fetch_mapping(client, "1TRZ"))
            await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(stream.closed)

    async def test_unexpected_content_encoding_and_transport_timeout_are_explicit(self):
        await self.assert_failure(lambda _: httpx.Response(200, stream=RawStream([b"anything"]),
                                                          headers={"Content-Encoding": "gzip"}), "sifts-invalid-response")
        def timeout(request):
            raise httpx.ReadTimeout("provider-private", request=request)
        await self.assert_failure(timeout, "sifts-timeout")


if __name__ == "__main__":
    unittest.main()
