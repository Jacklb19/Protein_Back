"""Interpretation through a simulated Groq transport: bounds, budget, provider failures and figure checks."""
import json
import struct
import unittest

import httpx
from fastapi.testclient import TestClient

from app.assistant import SCALE_FIGURES, SYSTEM_PROMPT, figures, known_figures, refusal, supported, supported_proportions, InterpretRequest
from app.config import Settings
from app.main import create_app
from app.rpc.gen.proteinforge.assistant.v1.assistant_service_pb import Fact, InterpretRequest as InterpretMessage, InterpretResponse, Subject

USER = "00000000-0000-4000-8000-000000000001"
TOKEN = {"Authorization": "Bearer user-token"}
CONNECT = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
ASSISTANT = "/proteinforge.assistant.v1.AssistantService"
SERVER_KEY = "gsk_server_side_test_key"
BODY = {
    "subject": "laboratory",
    "language": "es",
    "facts": [{"label": "pLDDT medio", "value": "93,39", "unit": "/ 100"}, {"label": "RMSD Cα", "value": "0,822", "unit": "Å"}, {"label": "Identidad posicional", "value": "42/76"}],
    "sequence": "MQIFVK",
}
ANSWER = "La variante tiene un pLDDT medio de 93,39 sobre 100 y un RMSD de 0,822 Å frente a 1UBQ, con 42 de 76 posiciones idénticas."


def completion(text=ANSWER, finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}]})


class AssistantTests(unittest.TestCase):
    def client(self, provider=None, key=SERVER_KEY, requests_per_minute=0):
        self.sent = []

        def transport(request):
            if request.url.host == "project.supabase.co":
                return httpx.Response(200, json={"id": USER, "email": "a@example.org"})
            self.assertEqual(str(request.url), "https://api.groq.com/openai/v1/chat/completions")
            # Only the server's key travels to the provider; the caller's token never does.
            self.assertEqual(request.headers["Authorization"], f"Bearer {SERVER_KEY}")
            self.assertNotIn("user-token", request.content.decode())
            self.sent.append(json.loads(request.content))
            return provider(request) if provider else completion()

        settings = Settings(supabase_url="https://project.supabase.co", supabase_anon_key="public-key", groq_api_key=key, assistant_requests_per_minute=requests_per_minute)
        return TestClient(create_app(settings, httpx.AsyncClient(transport=httpx.MockTransport(transport))))

    def post(self, client, body=BODY, headers=TOKEN):
        return client.post("/v1/assistant/interpret", json=body, headers=headers)

    def test_interprets_with_the_configured_model_and_no_caller_secrets(self):
        with self.client() as client:
            result = self.post(client)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertEqual(result.json()["text"], ANSWER)
        self.assertEqual(result.json()["model"], "openai/gpt-oss-120b")
        self.assertEqual(result.json()["provider"], "groq")
        self.assertTrue(result.json()["createdAt"])
        payload = self.sent[0]
        self.assertEqual(payload["model"], "openai/gpt-oss-120b")
        self.assertIs(payload["include_reasoning"], False)
        self.assertIn("Spanish", payload["messages"][0]["content"])
        self.assertEqual(json.loads(payload["messages"][1]["content"]), {"facts": BODY["facts"], "sequence": "MQIFVK"})

    def test_requires_identity_configuration_and_bounded_facts_before_calling_the_provider(self):
        with self.client(key="") as client:
            self.assertEqual(self.post(client, headers={}).status_code, 401)
            missing = self.post(client)
        self.assertEqual(missing.status_code, 503)
        self.assertIn("assistant-not-configured", missing.text)
        invalid = [
            {**BODY, "facts": []},
            {**BODY, "facts": BODY["facts"] * 30},
            {**BODY, "language": "fr"},
            {**BODY, "subject": "prediction"},
            {**BODY, "sequence": "MQ1FVK"},
            {**BODY, "facts": [{"label": "pI", "value": "4,81\n"}]},
            {**BODY, "extra": True},
        ]
        with self.client() as client:
            statuses = [self.post(client, body).status_code for body in invalid]
        self.assertEqual(statuses, [422] * len(invalid))
        self.assertEqual(self.sent, [])

    def test_refuses_an_answer_with_a_figure_absent_from_the_facts(self):
        invented = "La variante tiene un pLDDT medio de 95,2 y una estabilidad de 41,7 kcal/mol."
        with self.client(lambda _: completion(invented)) as client:
            result = self.post(client)
        self.assertEqual(result.status_code, 502)
        self.assertIn("assistant-unverified-figure", result.text)
        self.assertNotIn("41,7", result.text)

    def test_refuses_a_sign_change_in_a_supplied_descriptor(self):
        body = {**BODY, "facts": [{"label": "GRAVY", "value": "-0,532"}]}
        with self.client(lambda _: completion("El GRAVY calculado es 0,532.")) as client:
            result = self.post(client, body)
        self.assertEqual(result.status_code, 502)
        self.assertEqual(result.json()["detail"], "assistant-unverified-figure")

    def test_refuses_derived_proportions_even_when_every_written_figure_is_supported(self):
        answers = [
            "La identidad es 42 de 76, menos de la mitad de los residuos.",
            "Hay cambios en más de la mitad de los residuos, con identidad 42 de 76.",
            "The identity is 42 of 76, fewer than half of the residues.",
            "Mean pLDDT is 93,39, so most residues have high confidence.",
            "La mayoría de residuos tiene alta confianza, con media 93,39.",
        ]
        for answer in answers:
            with self.subTest(answer=answer), self.client(lambda _: completion(answer)) as client:
                result = self.post(client)
            self.assertEqual(result.status_code, 502)
            self.assertEqual(result.json()["detail"], "assistant-unverified-figure")
            self.assertNotIn(answer, result.text)

    def test_a_refusal_logs_only_kinds_and_counts(self):
        answer = "La identidad es 42 de 76, alrededor del 55 %, y la mayoría de los residuos confía."
        with self.assertLogs("app.assistant", "WARNING") as logs, self.client(lambda _: completion(answer)) as client:
            result = self.post(client)
        self.assertEqual(result.json()["detail"], "assistant-unverified-figure")
        line = " ".join(logs.output)
        self.assertIn("subject=laboratory language=es figure-percent=1 verbal-mayoria=1", line)
        for private in ("55", "42", "MQIFVK", "residuos"):
            self.assertNotIn(private, line)

    def test_preserves_a_verbal_quantity_explicitly_supplied_as_a_fact(self):
        body = {**BODY, "facts": [*BODY["facts"], {"label": "Clasificación calculada", "value": "Más de la mitad"}]}
        with self.client(lambda _: completion("Más de la mitad, según la clasificación recibida.")) as client:
            result = self.post(client, body)
        self.assertEqual(result.status_code, 200)

    def test_rpc_refuses_the_same_unverified_verbal_proportion(self):
        message = {"subject": "SUBJECT_LABORATORY", "language": "es", "facts": BODY["facts"]}
        with self.client(lambda _: completion("La identidad es 42 de 76, menos de la mitad.")) as client:
            result = client.post(f"{ASSISTANT}/Interpret", headers={**CONNECT, **TOKEN}, content=json.dumps(message))
        self.assertEqual(result.json(), {"code": "unavailable", "message": "assistant-unverified-figure"})

    def test_translates_provider_failures_without_leaking_them(self):
        def timeout(_):
            raise httpx.ReadTimeout("slow")
        cases = [
            (lambda _: httpx.Response(429), 429, "assistant-rate-limit"),
            (lambda _: httpx.Response(500, text="private provider trace"), 502, "assistant-provider-error"),
            (lambda _: httpx.Response(200, json={"choices": []}), 502, "assistant-invalid-response"),
            (lambda _: completion("   "), 502, "assistant-invalid-response"),
            (lambda _: completion(ANSWER, finish="length"), 502, "assistant-incomplete"),
            (timeout, 504, "assistant-timeout"),
        ]
        for provider, status, detail in cases:
            with self.client(provider) as client:
                result = self.post(client)
            self.assertEqual(result.status_code, status, detail)
            self.assertIn(detail, result.text)
            self.assertNotIn("provider trace", result.text)

    def test_one_caller_has_a_budget_and_a_malformed_request_does_not_spend_it(self):
        with self.client(requests_per_minute=1) as client:
            self.assertEqual(self.post(client, {**BODY, "language": "fr"}).status_code, 422)
            self.assertEqual(self.post(client).status_code, 200)
            refused = self.post(client)
        self.assertEqual(refused.status_code, 429)
        self.assertIn("assistant-rate-limit", refused.text)
        self.assertEqual(len(self.sent), 1)

    def test_rpc_matches_rest_over_connect_and_grpc_web(self):
        message = {"subject": "SUBJECT_LABORATORY", "language": "es", "facts": BODY["facts"], "sequence": "MQIFVK"}
        with self.client() as client:
            connect = client.post(f"{ASSISTANT}/Interpret", headers={**CONNECT, **TOKEN}, content=json.dumps(message))
            unspecified = client.post(f"{ASSISTANT}/Interpret", headers={**CONNECT, **TOKEN}, content=json.dumps({**message, "subject": "SUBJECT_UNSPECIFIED"}))
            facts = [Fact(**fact) for fact in BODY["facts"]]
            binary = InterpretMessage(subject=Subject.LABORATORY, language="en", facts=facts).to_binary()
            web = client.post(f"{ASSISTANT}/Interpret", headers={"Content-Type": "application/grpc-web+proto", "X-Grpc-Web": "1", **TOKEN}, content=struct.pack(">BI", 0, len(binary)) + binary)
        self.assertEqual(connect.status_code, 200)
        self.assertEqual(connect.json()["text"], ANSWER)
        self.assertEqual(connect.json()["provider"], "groq")
        self.assertEqual(unspecified.json()["code"], "invalid_argument")
        self.assertEqual(json.loads(self.sent[0]["messages"][1]["content"]), {"facts": BODY["facts"], "sequence": "MQIFVK"})
        # The binary request carried no sequence, and none is sent to the provider.
        self.assertEqual(json.loads(self.sent[-1]["messages"][1]["content"]), {"facts": BODY["facts"]})
        self.assertIn("English", self.sent[-1]["messages"][0]["content"])
        _flags, length = struct.unpack(">BI", web.content[:5])
        self.assertEqual(InterpretResponse.from_binary(web.content[5:5 + length]).text, ANSWER)

    def test_missing_configuration_has_a_stable_rpc_error_without_calling_the_provider(self):
        message = {"subject": "SUBJECT_ANALYSIS", "language": "es", "facts": BODY["facts"]}
        binary = InterpretMessage(subject=Subject.ANALYSIS, language="es", facts=[Fact(**fact) for fact in BODY["facts"]]).to_binary()
        with self.client(key="") as client:
            connect = client.post(f"{ASSISTANT}/Interpret", headers={**CONNECT, **TOKEN}, content=json.dumps(message))
            web = client.post(f"{ASSISTANT}/Interpret", headers={"Content-Type": "application/grpc-web+proto", "X-Grpc-Web": "1", **TOKEN}, content=struct.pack(">BI", 0, len(binary)) + binary)
        self.assertEqual(connect.status_code, 400)
        self.assertEqual(connect.json(), {"code": "failed_precondition", "message": "assistant-not-configured"})
        flags, length = struct.unpack(">BI", web.content[:5])
        self.assertEqual(flags, 0x80)
        self.assertEqual(web.content[5:5 + length].decode(), "grpc-status: 9\r\ngrpc-message: assistant-not-configured\r\n")
        self.assertEqual(self.sent, [])


class PromptRuleTests(unittest.TestCase):
    """The rules added after the 1 Nov review, pinned so they are not dropped silently."""

    def test_prompt_asks_what_measures_report_and_forbids_outside_figures_and_inferences(self):
        for language in ("Spanish", "English"):
            prompt = SYSTEM_PROMPT.format(subject="a protein design laboratory", language=language)
            self.assertIn(f"Write in {language}.", prompt)
            self.assertIn("what each measure reports for these values", prompt)
            self.assertNotIn("what these values suggest", prompt)
            self.assertIn("Cite no threshold, range or reference value other than those in the glossary", prompt)
            self.assertIn("Never say that all, every, none, or any share of the residues reach a level because of a mean", prompt)
            self.assertIn("Do not compare with variants, structures, references or results that are not in the facts", prompt)
            self.assertIn("Never claim that the protein folds, folds correctly, is stable or has a function", prompt)

    def test_no_ptm_bound_is_taught_or_allowed(self):
        # A pTM cut-off would be a new scientific figure; the glossary keeps only the 0 to 1 scale.
        self.assertIn("from 0 to 1. It is not a comparison", SYSTEM_PROMPT)
        self.assertNotIn("0.5", SYSTEM_PROMPT)
        self.assertEqual(SCALE_FIGURES, (40, 50, 70, 90, 100))

class FigureTests(unittest.TestCase):
    def known(self, *values, sequence=None):
        return known_figures(InterpretRequest.model_validate({"subject": "analysis", "language": "es", "facts": [{"label": "dato", "value": value} for value in values], "sequence": sequence}))

    def accepted(self, text, known):
        return all(supported(written, known) for written in figures(text))

    def test_accepts_given_values_their_roundings_and_both_decimal_conventions(self):
        known = self.known("8.445,71", "93,39", "0,822", "1.211", "-0,532")
        for text in ["Masa 8.445,71 Da", "Masa 8 445,71 Da", "masa de 8 446 Da", "masa de 8445.71", "pLDDT de 93,4", "un RMSD de 0.82", "1,211 Å", "GRAVY -0,532", "unos 8.446 Da"]:
            self.assertTrue(self.accepted(text, known), text)

    def test_rejects_new_figures_but_not_identifiers_or_small_counts(self):
        known = self.known("93,39", sequence="MQIFVK")
        self.assertTrue(self.accepted("1UBQ, E6K y A30 en las 3 variantes; 6 residuos y 20 aminoácidos.", known))
        for text in ["pLDDT de 93,40", "pLDDT de 94", "unos 120 residuos", "0,5 de diferencia"]:
            self.assertFalse(self.accepted(text, known), text)

    def test_allows_the_scale_bounds_the_glossary_teaches(self):
        # Explaining a measure needs its scale: "below the threshold of 40", "out of 100".
        known = self.known("20,91")
        self.assertTrue(self.accepted("Inestabilidad 20,91, por debajo del umbral de 40.", known))
        self.assertTrue(self.accepted("Un pLDDT sobre 100; por encima de 90 es muy alto y bajo 50 muy bajo.", known))
        self.assertFalse(self.accepted("La masa es 8.445,71 Da.", known))

    def test_preserves_signs_including_a_typographic_minus(self):
        known = self.known("-0,532", "84,46")
        for text in ["GRAVY -0,532", "GRAVY −0,532", "GRAVY -0,53", "masa +84,46"]:
            self.assertTrue(self.accepted(text, known), text)
        for text in ["GRAVY 0,532", "GRAVY +0,532", "masa -84,46", "−100", "-42"]:
            self.assertFalse(self.accepted(text, known), text)

    def test_a_long_numeric_fact_does_not_break_checking_other_figures(self):
        known = self.known("9" * 100 + ".12")
        self.assertFalse(self.accepted("Una medida de 84,46.", known))


def laboratory(sample, plddt, rmsd, ptm, identical, language="es"):
    """The facts the laboratory screen sends for an unedited public 1UBQ variant (1 Nov 2026)."""
    labels = {
        "es": ("Muestra", f"Variante {sample}", "Referencia experimental", "Longitud", "residuos", "Confianza local media / 100", "RMSD Cα frente a la referencia", "Identidad posicional con la referencia", f"{identical} de 76", "Posiciones protegidas"),
        "en": ("Sample", f"Variant {sample}", "Experimental reference", "Length", "residues", "Mean local confidence / 100", "Cα RMSD against the reference", "Positional identity with the reference", f"{identical} of 76", "Protected positions"),
    }[language]
    sample_label, sample_value, reference, length, residues, confidence, rmsd_label, identity, identity_value, protected = labels
    return InterpretRequest.model_validate({"subject": "laboratory", "language": language, "facts": [
        {"label": sample_label, "value": sample_value}, {"label": reference, "value": "1UBQ"},
        {"label": length, "value": "76", "unit": residues}, {"label": confidence, "value": plddt},
        {"label": rmsd_label, "value": rmsd, "unit": "Å"}, {"label": "pTM", "value": ptm},
        {"label": identity, "value": identity_value}, {"label": protected, "value": "0"},
    ]})


VARIANTS = [laboratory(1, "93,39", "0,822", "0,863", 42), laboratory(2, "93,47", "0,751", "0,861", 41), laboratory(3, "92,92", "1,211", "0,858", 40)]


class PublicVariantReplayTests(unittest.TestCase):
    """Plausible answers for the three public variants refused on 1 Nov, replayed offline."""

    def test_faithful_answers_are_accepted_for_each_variant(self):
        for request in VARIANTS:
            plddt, rmsd, ptm, identity = (request.facts[index].value for index in (3, 4, 5, 6))
            answer = (
                f"Estos resultados describen una variante diseñada sobre 1UBQ. La confianza local media es {plddt} sobre 100, "
                f"por encima de 90, el tramo muy alto de una escala de 0 a 100. La pTM es {ptm}, en una escala de 0 a 1. "
                f"El RMSD Cα es {rmsd} Å y la identidad posicional es {identity}; hay 0 posiciones protegidas en 76 residuos."
            )
            with self.subTest(sample=request.facts[0].value):
                self.assertEqual(refusal(answer, request), {})

    def test_invented_thresholds_derived_counts_and_proportions_stay_refused(self):
        cases = [
            ("La pTM supera el umbral habitual de 0,5.", {"figure-decimal": 1}),
            ("Un RMSD inferior a 1,5 Å se considera muy similar.", {"figure-decimal": 1}),
            ("Así que 34 posiciones cambian respecto a 1UBQ.", {"figure-integer": 1}),
            ("Equivale a un 55 % de identidad.", {"figure-percent": 1}),
            ("La mayoría de los residuos tiene confianza muy alta.", {"verbal-mayoria": 1}),
        ]
        for request in VARIANTS:
            for answer, reasons in cases:
                with self.subTest(sample=request.facts[0].value, answer=answer):
                    self.assertEqual(refusal(answer, request), reasons)
        self.assertEqual(refusal("La confianza media es 93,3.", VARIANTS[0]), {"figure-near-known": 1})

    def test_english_ordinals_and_upper_bounds_are_not_proportions(self):
        request = laboratory(3, "92.92", "1.211", "0.858", 40, language="en")
        for answer in ["This is the third variant designed on 1UBQ.", "The mean confidence of 92.92 is at most 100."]:
            self.assertTrue(supported_proportions(answer, request), answer)
        for answer in ["About a third of the residues differ.", "Most residues are confident.", "At most half of the positions differ.", "The third of the positions that changed.", "Variant 3 is the most similar."]:
            self.assertFalse(supported_proportions(answer, request), answer)


if __name__ == "__main__":
    unittest.main()
