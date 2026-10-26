"""Inspect one public mapping; never imports settings, credentials or application routes."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import ssl

import httpx

from app.sifts import SiftsError, fetch_mapping, normalise_mapping, source_urls

ROOT = Path(__file__).resolve().parents[1]


async def run(args):
    source_urls(args.pdb_id)
    if args.xml:
        mapping = normalise_mapping(Path(args.xml).read_bytes(),
                                    json.loads(Path(args.listing).read_text(encoding="utf-8")), args.pdb_id)
    else:
        # Use the operating system trust store, with hostname/certificate verification enabled.
        async with httpx.AsyncClient(verify=ssl.create_default_context()) as client:
            mapping = await fetch_mapping(client, args.pdb_id)
    encoded = (mapping.model_dump_json(by_alias=True, indent=2) + "\n").encode("utf-8")
    destination = ROOT / "artifacts" / "sifts" / f"{args.pdb_id.lower()}.mapping.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    print(json.dumps({"pdbId": mapping.pdb_id, "rows": len(mapping.rows),
                      "chains": sorted({row.label_asym_id for row in mapping.rows}),
                      "accessions": sorted({row.accession for row in mapping.rows}),
                      "releaseDate": mapping.provenance.release_date,
                      "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest(),
                      "file": str(destination), "sources": "offline files" if args.xml else "public HTTPS"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdb_id", nargs="?", default="1TRZ")
    parser.add_argument("--xml", help="Validate local source files without network")
    parser.add_argument("--listing", help="PDBe JSON matching --xml")
    args = parser.parse_args()
    if bool(args.xml) != bool(args.listing):
        parser.error("--xml and --listing must be supplied together")
    try:
        asyncio.run(run(args))
    except SiftsError as error:
        parser.exit(1, f"{error.code}\n")


if __name__ == "__main__":
    main()
