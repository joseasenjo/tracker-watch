"""Pasting the Web3Forms notice into the panel: one address, a ticked consent box, no robots, nothing sent."""
from datetime import date

import pytest

from traceguard import mailing, ops

NOTICE = """New Submission from Tracker Watch website
Subscribe to Tracker Watch Weekly
To: asenjo.jose@hotmail.com

email: gel472@hotmail.com
message: Request for the Tracker Watch weekly email, sent from the subscribe page. The person ticked the consent box.
consent: yes
interest: yes
Sent via web3forms.com  hello@web3forms.com
"""
TODAY = date(2026, 10, 18)


def test_a_real_notice_becomes_one_pending_subscriber_with_its_consent_record(tmp_path):
    store = tmp_path / "subs.json"
    done = ops.import_web_request(NOTICE, path=store, today=TODAY)
    assert done["email"] == "gel472@hotmail.com" and done["interest"] is True
    [entry] = mailing.load_subscribers({}, store)
    assert entry["email"] == "gel472@hotmail.com" and entry["status"] == "pending"
    assert "consent box ticked" in entry["consent"] and "2026-10-18" in entry["consent"] and entry["asked"] == "2026-10-18"


def test_it_reads_the_address_in_a_table_layout_and_without_the_optional_interest():
    text = "Email\n  Someone.Else@Example.org  \nconsent\nyes\n"
    assert ops.parse_web_request(text) == {"email": "someone.else@example.org", "consent": True, "interest": False}


def test_it_refuses_a_missing_consent_the_trap_field_and_an_unclear_address(tmp_path):
    with pytest.raises(ops.OpsError, match="consentimiento"):
        ops.parse_web_request("email: a@example.org\nmessage: The person ticked the consent box.")
    with pytest.raises(ops.OpsError, match="robot"):
        ops.parse_web_request("email: a@example.org\nconsent: yes\nbotcheck: yes")
    with pytest.raises(ops.OpsError, match="ninguna dirección"):
        ops.parse_web_request("consent: yes")
    with pytest.raises(ops.OpsError, match="varias direcciones"):
        ops.parse_web_request("a@example.org and b@example.org\nconsent: yes")
    assert not (tmp_path / "subs.json").exists()


def test_the_owner_and_the_service_addresses_are_never_taken_for_the_person():
    text = "To: asenjo.jose@hotmail.com\nFrom: noreply@web3forms.com\nmine@gmail.example\nconsent: yes"
    with pytest.raises(ops.OpsError, match="ninguna dirección"):
        ops.parse_web_request(text, own=("mine@gmail.example",))


def test_importing_the_same_person_twice_is_refused_and_keeps_the_first_record(tmp_path):
    store = tmp_path / "subs.json"
    ops.import_web_request(NOTICE, path=store, today=TODAY)
    with pytest.raises(ops.OpsError, match="already"):
        ops.import_web_request(NOTICE, path=store, today=date(2026, 10, 19))
    [entry] = mailing.load_subscribers({}, store)
    assert entry["asked"] == "2026-10-18"


REAL_NOTICE = """Hello,

A new form has been submitted on your website. Details below.


Message
Request for the Tracker Watch weekly email, sent from the subscribe page. The person ticked the consent box.
Email
prueba-claude@example.org
Consent
yes
"""


def test_the_notice_exactly_as_web3forms_sent_it_on_2026_10_18_is_read(tmp_path):
    assert ops.parse_web_request(REAL_NOTICE) == {"email": "prueba-claude@example.org", "consent": True, "interest": False}
    assert ops.parse_web_request(REAL_NOTICE + "Interest\nyes\n")["interest"] is True
    with pytest.raises(ops.OpsError, match="consentimiento"):
        ops.parse_web_request(REAL_NOTICE.replace("Consent\nyes\n", ""))
