from samplebuddy import naming


def test_prefix_roundtrip():
    name = naming.prefixed("Work - Gang Starr", 92, "Am")
    assert name == "92 BPM Am - Work - Gang Starr"
    assert naming.parse_prefix(name) == (92, "Am", "Work - Gang Starr")


def test_parse_prefix_variants():
    assert naming.parse_prefix("83 BPM C#m - Down And Out")[1] == "C#m"
    assert naming.parse_prefix("128 BPM 8A - House Tune")[1] == "8A"
    assert naming.parse_prefix("174 BPM 12B - DnB")[0] == 174
    assert naming.parse_prefix("Work - Gang Starr") is None
    assert naming.parse_prefix("2014 Remaster - Song") is None


def test_fold_bpm():
    assert naming.fold_bpm(184, 65, 150) == 92
    assert naming.fold_bpm(46, 65, 150) == 92
    assert naming.fold_bpm(174, 85, 180) == 174


def test_key_label():
    assert naming.key_label("A", "minor") == "Am"
    assert naming.key_label("A", "minor", "camelot") == "8A"
    assert naming.key_label("Ab", "major", "camelot") == "4B"
    assert naming.key_label("C#", "minor", "camelot") == "12A"
