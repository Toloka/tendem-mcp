"""Payload parsing against the live server's shapes."""

from __future__ import annotations

from langchain_tendem import Contract, TaskSnapshot
from langchain_tendem.models import format_money, parse_money

# ------------------------------------------------------------------- money


def test_parse_money_object_shape() -> None:
    """The live server sends money as an object."""
    assert parse_money({"amount": 3, "currency": "USD", "formatted": "$3.00"}) == (
        3.0,
        "USD",
        "$3.00",
    )


def test_parse_money_plain_number() -> None:
    assert parse_money(12.5) == (12.5, None, None)


def test_parse_money_formatted_string() -> None:
    amount, currency, formatted = parse_money("$1,200.50")
    assert amount == 1200.5
    assert currency is None
    assert formatted == "$1,200.50"


def test_parse_money_rejects_junk_quietly() -> None:
    assert parse_money(None) == (None, None, None)
    assert parse_money(True) == (None, None, None)
    assert parse_money([1]) == (None, None, None)
    assert parse_money({"amount": "n/a"}) == (None, None, None)


def test_format_money_prefers_the_server_formatting() -> None:
    assert format_money(3.0, "USD", "$3.00") == "$3.00"
    assert format_money(3.0, None, None) == "$3.00"
    assert format_money(3.0, "EUR", None) == "3.00 EUR"
    assert format_money(None, None, None) is None


# ---------------------------------------------------------------- snapshot


def test_snapshot_parses_the_live_get_task_shape() -> None:
    snapshot = TaskSnapshot.from_payload(
        {
            "task_id": "t1",
            "name": "Proofread a short paragraph",
            "status": "LISTENING",
            "ready_for_approval": True,
            "price": {"amount": 3, "currency": "USD", "formatted": "$3.00"},
            "latest_chat_offset": 2,
            "next_action": "await_user_approval",
            "poll_after_seconds": None,
            "guidance": "Show the user; if they agree, call approve_task.",
        }
    )

    assert snapshot.ready_for_approval is True
    assert snapshot.price == 3.0
    assert snapshot.price_formatted == "$3.00"
    assert snapshot.latest_chat_offset == 2
    assert snapshot.name == "Proofread a short paragraph"


# ---------------------------------------------------------------- contract


LIVE_CONTRACT = {
    "task_id": "t1",
    "state": "available",
    "contract": {
        "input_prompt": "Perform a single minimal proofreading pass...",
        "quality_criteria": [
            {"name": "The delivered result is written in English"},
            {"name": "The result includes the edited paragraph"},
        ],
        "title": "Edit paragraph",
        "marketing_points": ["Human editor reviews the paragraph"],
    },
    "price": {"amount": 3, "currency": "USD", "formatted": "$3.00"},
}


def test_contract_parses_the_live_nested_shape() -> None:
    contract = Contract.from_payload(LIVE_CONTRACT)

    assert contract.state == "available"
    assert contract.title == "Edit paragraph"
    assert contract.task_description is not None
    assert contract.task_description.startswith("Perform a single")
    assert contract.price == 3.0
    assert contract.display_price == "$3.00"
    assert contract.is_quoted is True
    assert contract.fields["marketing_points"] == ["Human editor reviews the paragraph"]

    summary = contract.summary()
    assert "Edit paragraph" in summary
    assert "$3.00" in summary
    assert "written in English" in summary


def test_contract_estimating_state_is_not_quoted() -> None:
    contract = Contract.from_payload(
        {
            "task_id": "t1",
            "state": "estimating",
            "contract": {"title": "Edit paragraph", "input_prompt": "..."},
            "price": None,
        }
    )

    assert contract.is_quoted is False
    assert "do not approve" in contract.summary()


def test_contract_tolerates_the_flat_legacy_shape() -> None:
    contract = Contract.from_payload(
        {
            "task_id": "t1",
            "title": "Teardown",
            "task_description": "Scope text",
            "price": 120.0,
            "currency": "USD",
        }
    )

    assert contract.title == "Teardown"
    assert contract.task_description == "Scope text"
    assert contract.price == 120.0
