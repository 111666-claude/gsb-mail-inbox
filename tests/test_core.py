import unittest

from mailinbox.core import Inbox, Mail, MailError


class DeliverTest(unittest.TestCase):
    def test_deliver_increases_size(self):
        inbox = Inbox()
        inbox.deliver(Mail("m-1", 60000, []))
        self.assertEqual(inbox.size(0), 1)

    def test_dropped_starts_at_zero(self):
        self.assertEqual(Inbox().dropped, 0)


class ClaimTest(unittest.TestCase):
    def test_claim_returns_attachments(self):
        inbox = Inbox()
        inbox.deliver(Mail("m-1", 60000, [("gold", 3)]))
        self.assertEqual(inbox.claim("m-1", 0), [("gold", 3)])

    def test_unknown_mail_raises(self):
        with self.assertRaises(MailError):
            Inbox().claim("missing", 0)


class PurgeTest(unittest.TestCase):
    def test_purge_keeps_live_mail(self):
        inbox = Inbox()
        inbox.deliver(Mail("m-1", 60000, []))
        inbox.purge(1000)
        self.assertEqual(inbox.size(1000), 1)


if __name__ == "__main__":
    unittest.main()
