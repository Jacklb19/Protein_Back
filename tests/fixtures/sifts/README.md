# Extractos de fuentes públicas SIFTS

Estos dos archivos conservan ocho correspondencias explícitas de 1TRZ: extremos de las cadenas A/B/C/D. No son una respuesta completa y no permiten rellenar los huecos. Se reutiliza el extracto trazable del frontend, commit local `4efae52a7c82494a55ce0cdfc2bc468e6fca4890`; las pruebas del backend son independientes de la existencia del frontend.

Originales públicos consultados el 23 de octubre de 2026:

- [SIFTS XML gzip](https://ftp.ebi.ac.uk/pub/databases/msd/sifts/xml/1trz.xml.gz): 9.620 bytes, SHA-256 `5495bec6290b908ea48a19ef1f7cb27233e9e741daddee2d9855a58bce1e461b`. XML original descomprimido de 253.185 bytes, SHA-256 `b152a6ee038c603406ae0da06f859d58f23f9c83155dd371406f4eb3501e611e`, fecha de documento 2026-10-18.
- [PDBe residue listing](https://www.ebi.ac.uk/pdbe/api/pdb/entry/residue_listing/1trz): 13.315 bytes, SHA-256 `31bebbc3a6f0e14cdae4a2ac476c4fd44072408f2be0125ffb31ba139dc2b4b4`.

Se retiraron anotaciones de otras bases, moléculas ajenas y filas intermedias. Se conservaron claves de cadena/entidad/segmento, nombres y números explícitos, inserciones y fecha. Normalizando CRLF a LF, `1trz.xml` tiene SHA-256 `feba210f06b5d9792882324a23d45c36bda9620674ce03a60ebf4bf100ceb705` y `1trz.json` `d8ce2eaa8a4b7c1048f3ccbba7b9061cd626214f674c7841319a7d0e52cf22c3`.

Procedencia y atribución: PDBe–UniProt/SIFTS, EMBL-EBI. El original incluye copyright de EMBL-EBI/PDBe-UniProt y atribución a Dana, Tyagi, Choudhary, Gutmanas, O'Donovan, Martin y Velankar. [Recurso SIFTS](https://www.ebi.ac.uk/pdbe/docs/sifts/), [términos de EMBL-EBI](https://www.ebi.ac.uk/about/terms-of-use/). No se asigna por inferencia la licencia de las coordenadas PDB. Los casos alterados por las pruebas son sintéticos y no nuevas observaciones experimentales.
