from yoga_deck.diagnostics.ocr_capture import run_guided_ocr_capture


def test_guided_ocr_summary_records_only_timing_and_user_verification() -> None:
    answers = iter((True, False))
    prompts = []
    ticks = iter((10.0, 10.125, 20.0, 20.25))

    summary = run_guided_ocr_capture(
        trials=2,
        capture=lambda: None,
        prompt=prompts.append,
        confirm=lambda: next(answers),
        clock=lambda: next(ticks),
    )

    assert summary.as_dict() == {
        "attempted": 2,
        "verified": 1,
        "unverified": 1,
        "command_failed": 0,
        "latency_ms": {"count": 2, "median": 187.5, "max": 250.0},
    }
    assert all("clipboard" not in prompt.lower() for prompt in prompts)


def test_guided_ocr_failure_does_not_prompt_for_or_retain_text() -> None:
    prompts = []

    summary = run_guided_ocr_capture(
        trials=1,
        capture=lambda: (_ for _ in ()).throw(OSError("private OCR text")),
        prompt=prompts.append,
        confirm=lambda: (_ for _ in ()).throw(AssertionError("must not confirm")),
        clock=iter((1.0, 1.5)).__next__,
    )

    assert summary.as_dict()["command_failed"] == 1
    assert "private" not in repr(summary)
    assert len(prompts) == 1
