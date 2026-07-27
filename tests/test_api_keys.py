import os
import tempfile
import unittest

from weatherhub.api_keys import create_key, list_keys, revoke_key, verify_key
from weatherhub.db import init_db


class TestApiKeys(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, self.db_path)
        init_db(self.db_path)

    def test_created_key_verifies(self) -> None:
        key = create_key(self.db_path, "my-app")

        self.assertTrue(key.startswith("wh_"))
        self.assertTrue(verify_key(self.db_path, key))

    def test_unknown_key_does_not_verify(self) -> None:
        self.assertFalse(verify_key(self.db_path, "wh_not-a-real-key"))

    def test_empty_key_does_not_verify(self) -> None:
        self.assertFalse(verify_key(self.db_path, ""))

    def test_revoked_key_no_longer_verifies(self) -> None:
        key = create_key(self.db_path, "my-app")
        [row] = list_keys(self.db_path)

        self.assertTrue(revoke_key(self.db_path, row["id"]))
        self.assertFalse(verify_key(self.db_path, key))

    def test_revoking_unknown_id_returns_false(self) -> None:
        self.assertFalse(revoke_key(self.db_path, 9999))

    def test_list_keys_reflects_creation_and_revocation(self) -> None:
        create_key(self.db_path, "app-one")
        create_key(self.db_path, "app-two")

        keys = list_keys(self.db_path)
        self.assertEqual([k["label"] for k in keys], ["app-one", "app-two"])
        self.assertTrue(all(k["revoked_at"] is None for k in keys))

        revoke_key(self.db_path, keys[0]["id"])
        keys = list_keys(self.db_path)
        self.assertIsNotNone(keys[0]["revoked_at"])
        self.assertIsNone(keys[1]["revoked_at"])

    def test_verifying_key_stamps_last_used_at(self) -> None:
        key = create_key(self.db_path, "my-app")
        [row_before] = list_keys(self.db_path)
        self.assertIsNone(row_before["last_used_at"])

        verify_key(self.db_path, key)

        [row_after] = list_keys(self.db_path)
        self.assertIsNotNone(row_after["last_used_at"])


if __name__ == "__main__":
    unittest.main()
