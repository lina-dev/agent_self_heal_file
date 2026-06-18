from audio_repair.core.taxonomy import FAIL_FAST, Category, Tier


def test_has_26_categories():
    assert len(list(Category)) == 26


def test_codes_unique_and_1_to_26():
    codes = sorted(c.code for c in Category)
    assert codes == list(range(1, 27))


def test_tiers_assigned():
    assert Category.TRUNCATED_MISSING_MOOV.tier == Tier.HARD
    assert Category.AUDIO_IN_VIDEO.tier == Tier.DISCOVERY
    assert Category.INCORRECT_CHANNEL_COUNT.tier == Tier.INVALID_DOWNSTREAM
    assert Category.DURATION_GE_3H.tier == Tier.POLICY


def test_fail_fast_flags():
    assert Category.TRUNCATED_MISSING_MOOV.fail_fast is True
    assert Category.ZERO_BYTE_OR_NONMEDIA.fail_fast is True
    assert Category.DURATION_GE_3H.fail_fast is True
    assert Category.AUDIO_PHYSICALLY_ABSENT.fail_fast is True
    assert Category.DAMAGED_INDEX.fail_fast is False


def test_by_code():
    assert Category.by_code(10) is Category.AUDIO_IN_VIDEO


def test_fail_fast_set():
    assert Category.ZERO_BYTE_OR_NONMEDIA in FAIL_FAST
    assert Category.DAMAGED_INDEX not in FAIL_FAST
