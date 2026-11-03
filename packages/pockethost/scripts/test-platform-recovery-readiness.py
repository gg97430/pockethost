#!/usr/bin/env python3
import unittest
from platform_recovery_readiness import reviewed_inventory


class Readiness(unittest.TestCase):
    def setUp(self):
        self.original = ['a' * 15, 'b' * 15]

    def test_exact_assembly_still_required_by_default(self):
        self.assertEqual(reviewed_inventory(self.original, self.original), self.original)
        with self.assertRaises(ValueError):
            reviewed_inventory(self.original, self.original + ['c' * 15])

    def test_existing_migration_can_start_after_new_databases_are_created(self):
        self.assertEqual(reviewed_inventory(self.original, self.original + ['c' * 15], allow_additions=True), self.original + ['c' * 15])

    def test_a_missing_migrated_database_is_never_silently_accepted(self):
        with self.assertRaises(ValueError):
            reviewed_inventory(self.original, ['a' * 15, 'c' * 15], allow_additions=True)

    def test_duplicates_are_refused(self):
        with self.assertRaises(ValueError):
            reviewed_inventory(self.original, self.original * 2, allow_additions=True)

    def test_empty_and_invalid_paths_are_refused(self):
        for value in [[], ['../' + 'c' * 12], ['APP1' + 'a' * 11], [None]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                reviewed_inventory(self.original, value, allow_additions=True)


if __name__ == '__main__':
    unittest.main()
