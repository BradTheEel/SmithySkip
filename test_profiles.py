"""Check introduction resolution and compare generated profile artifacts."""
import argparse
import hashlib
import json
from pathlib import Path
import unittest

from generate_stations import resolve_intro_indices

BASE = Path(__file__).resolve().parent
NPC = '012'
INTRO = ('N012_seken_101_NPC', 'Introduction')
SECOND_INTRO = ('N012_seken_102_NPC', 'Second introduction')
SERVICE = ('N012_sisetu_101_NPC', 'Service')


class IntroductionTests(unittest.TestCase):
    def test_reordering_preserves_configured_key_order(self):
        keys = (INTRO[0], SECOND_INTRO[0])
        self.assertEqual(resolve_intro_indices(NPC, [INTRO, SERVICE, SECOND_INTRO], keys), [0, 2])
        self.assertEqual(resolve_intro_indices(NPC, [SERVICE, SECOND_INTRO, INTRO], keys), [2, 1])

    def test_insertion_moves_protected_indices(self):
        keys = (INTRO[0], SECOND_INTRO[0])
        original = [SERVICE, INTRO, SECOND_INTRO]
        inserted = ('N012_sisetu_102_NPC', 'Inserted service')
        self.assertEqual(resolve_intro_indices(NPC, original, keys), [1, 2])
        self.assertEqual(resolve_intro_indices(NPC, [inserted, *original], keys), [2, 3])

    def test_empty_configuration_preserves_no_rows(self):
        self.assertEqual(resolve_intro_indices(NPC, [SERVICE], ()), [])

    def test_invalid_keys_identify_npc_and_key(self):
        key = INTRO[0]
        cases = (
            ('missing', [SERVICE], (key,)),
            ('ambiguous', [INTRO, SERVICE, (key, 'Other introduction')], (key,)),
            ('duplicate configuration', [INTRO, SERVICE], (key, key)),
        )
        for case, rows, keys in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError) as error:
                    resolve_intro_indices(NPC, rows, keys)
                self.assertIn(f'NPC{NPC}', str(error.exception))
                self.assertIn(key, str(error.exception))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-audit', type=Path, help='Generated profile-audit.json from the baseline')
    parser.add_argument('--baseline-header', type=Path, help='Published profiles.h from the release')
    args = parser.parse_args()

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IntroductionTests)
    if not unittest.TextTestRunner().run(suite).wasSuccessful():
        return 1

    audit = json.loads((BASE / 'build/profile-audit.json').read_text(encoding='utf-8'))
    selected = sum(len(profile['entries']) for profile in audit)
    preserved = sum(profile['count'] - len(profile['entries']) for profile in audit)
    totals = (len(audit), selected, preserved)
    expected = (23, 1636, 945)
    if totals != expected:
        raise AssertionError(f'Profile totals {totals} differ from {expected}')
    print(f'PASS profile totals {totals[0]} / {totals[1]} / {totals[2]}')

    if args.baseline_audit is not None:
        baseline_audit = json.loads(args.baseline_audit.read_text(encoding='utf-8'))
        if audit != baseline_audit:
            raise AssertionError(f'Generated audit differs from {args.baseline_audit}')
        print('PASS profile audit matches all baseline fields')

    header = (BASE / 'src/profiles.h').read_bytes()
    digest = hashlib.sha256(header).hexdigest()
    if args.baseline_header is not None:
        baseline_header = args.baseline_header.read_bytes()
        baseline_digest = hashlib.sha256(baseline_header).hexdigest()
        if digest != baseline_digest or header != baseline_header:
            raise AssertionError(f'Generated header SHA-256 {digest} differs from release {baseline_digest}')
        print(f'PASS profiles.h matches the release byte for byte (SHA-256 {digest})')
    else:
        print(f'profiles.h SHA-256 {digest}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
