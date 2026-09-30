"""收件箱回归测试：领取幂等、过期回收、容量淘汰、查询代价，及参考实现对照。"""
import contextlib
import io
import random
import unittest

from mailinbox import cli
from mailinbox.core import Inbox, Mail, MailError


class IdempotentClaimTest(unittest.TestCase):
    def test_second_claim_returns_empty(self):
        inbox = Inbox()
        inbox.deliver(Mail("m-1", 60000, [("gold", 3), ("gem", 2)]))
        first = inbox.claim("m-1", 0)
        second = inbox.claim("m-1", 0)
        self.assertEqual(first, [("gold", 3), ("gem", 2)])
        self.assertFalse(second)
        self.assertEqual(second, [])

    def test_attachments_granted_only_once(self):
        inbox = Inbox()
        inbox.deliver(Mail("m-1", 60000, [("gold", 3)]))
        granted = []
        for _ in range(5):
            granted.extend(inbox.claim("m-1", 0))
        self.assertEqual(granted, [("gold", 3)])

    def test_claim_unknown_raises(self):
        with self.assertRaises(MailError):
            Inbox().claim("missing", 0)

    def test_claim_expired_raises(self):
        inbox = Inbox()
        inbox.deliver(Mail("m-1", 1000, [("gold", 1)]))
        with self.assertRaises(MailError):
            inbox.claim("m-1", 1000)
        with self.assertRaises(MailError):
            inbox.claim("m-1", 1001)
        self.assertEqual(inbox.claim("m-1", 999), [("gold", 1)])

    def test_claim_purged_or_evicted_raises(self):
        inbox = Inbox(capacity=1)
        inbox.deliver(Mail("old", 60000, [("gold", 1)]))
        inbox.deliver(Mail("new", 60000, [("gold", 2)]))
        with self.assertRaises(MailError):
            inbox.claim("old", 0)

        inbox2 = Inbox()
        inbox2.deliver(Mail("gone", 1000, [("gold", 1)]))
        inbox2.purge(1000)
        with self.assertRaises(MailError):
            inbox2.claim("gone", 0)


class ExpirePurgeTest(unittest.TestCase):
    def test_expire_boundary_is_inclusive(self):
        mail = Mail("m-1", 1000, [])
        self.assertFalse(Inbox().expired(mail, 999))
        self.assertTrue(Inbox().expired(mail, 1000))
        self.assertTrue(Inbox().expired(mail, 1001))

    def test_purge_removes_expired_and_counts(self):
        inbox = Inbox()
        inbox.deliver(Mail("a", 1000, []))
        inbox.deliver(Mail("b", 2000, []))
        inbox.deliver(Mail("c", 3000, []))
        self.assertEqual(inbox.size(1000), 2)
        self.assertEqual(inbox.purge(1000), 1)
        self.assertEqual(inbox.purge(1000), 0)
        self.assertEqual(inbox.size(2500), 1)
        self.assertEqual(inbox.purge(3000), 2)
        self.assertEqual(inbox.size(3000), 0)

    def test_size_excludes_expired_before_purge(self):
        inbox = Inbox()
        inbox.deliver(Mail("a", 1000, []))
        inbox.deliver(Mail("b", 2000, []))
        self.assertEqual(inbox.size(0), 2)
        self.assertEqual(inbox.size(1500), 1)
        self.assertEqual(inbox.size(2000), 0)
        self.assertEqual(inbox.purge(2000), 2)

    def test_purge_keeps_live_mail(self):
        inbox = Inbox()
        inbox.deliver(Mail("m-1", 60000, []))
        self.assertEqual(inbox.purge(1000), 0)
        self.assertEqual(inbox.size(1000), 1)


class CapacityTest(unittest.TestCase):
    def test_evicts_oldest_and_counts_dropped(self):
        inbox = Inbox(capacity=2)
        for index in range(5):
            inbox.deliver(Mail("m-%d" % index, 60000, [("x", index)]))
        self.assertEqual(inbox.size(0), 2)
        self.assertEqual(inbox.dropped, 3)
        self.assertEqual(inbox.claim("m-3", 0), [("x", 3)])
        self.assertEqual(inbox.claim("m-4", 0), [("x", 4)])

    def test_capacity_after_expiry_and_purge(self):
        inbox = Inbox(capacity=2)
        inbox.deliver(Mail("a", 10, []))
        inbox.deliver(Mail("b", 10, []))
        inbox.purge(10)
        inbox.deliver(Mail("c", 60000, []))
        inbox.deliver(Mail("d", 60000, []))
        self.assertEqual(inbox.size(0), 2)
        self.assertEqual(inbox.dropped, 0)

    def test_purge_does_not_increase_dropped(self):
        inbox = Inbox(capacity=10)
        for index in range(4):
            inbox.deliver(Mail("m-%d" % index, index * 10, []))
        inbox.purge(1000)
        self.assertEqual(inbox.dropped, 0)
        self.assertEqual(inbox.size(0), 0)


class QueryCostTest(unittest.TestCase):
    def test_repeated_claim_is_indexed(self):
        inbox = Inbox(capacity=100000)
        for index in range(2000):
            inbox.deliver(Mail("m-%d" % index, 600000, []))
        for _ in range(2000):
            inbox.claim("m-1999", 0)
        self.assertLessEqual(inbox.scanned_count(), 4000)

    def test_repeated_size_scans_nothing_while_live(self):
        inbox = Inbox(capacity=100000)
        for index in range(5000):
            inbox.deliver(Mail("m-%d" % index, 1000 + index, []))
        before = inbox.scanned_count()
        for _ in range(1000):
            self.assertEqual(inbox.size(0), 5000)
        self.assertEqual(inbox.scanned_count(), before)

    def test_purge_touches_only_expired(self):
        inbox = Inbox(capacity=100000)
        for index in range(5000):
            inbox.deliver(Mail("m-%d" % index, 1000 + index, []))
        before = inbox.scanned_count()
        self.assertEqual(inbox.purge(1500), 501)
        self.assertLessEqual(inbox.scanned_count() - before, 1000)

    def test_mixed_workload_scanned_stays_linear(self):
        inbox = Inbox(capacity=1000)
        rng = random.Random(42)
        total = 3000
        for index in range(total):
            inbox.deliver(Mail("m-%d" % index, rng.randint(0, 4000), []))
        for _ in range(total):
            now = rng.randint(0, 4000)
            try:
                inbox.claim("m-%d" % rng.randint(0, total - 1), now)
            except MailError:
                pass
            inbox.size(now)
            inbox.purge(now)
        # 全表扫描会产生 O(ops * mails) 量级；正确实现每封邮件至多被触碰常数次。
        self.assertLessEqual(inbox.scanned_count(), 6 * total)


class CliSampleTest(unittest.TestCase):
    def run_sample(self, sample):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            cli.main(["--sample", sample])
        return stdout.getvalue().strip()

    def test_claim_sample(self):
        self.assertEqual(self.run_sample("claim"), "first=true second=false items=3")

    def test_expire_sample(self):
        self.assertEqual(self.run_sample("expire"), "purged=1 size=0")

    def test_capacity_sample(self):
        self.assertEqual(self.run_sample("capacity"), "size=2 dropped=3")

    def test_scan_sample(self):
        output = self.run_sample("scan")
        self.assertTrue(output.startswith("scanned="))
        self.assertLessEqual(int(output.split("=", 1)[1]), 4000)


class RefInbox:
    """参考实现：列表 + 全量扫描，直白地表达「索引领取 + 到期回收 + 容量淘汰」口径。"""

    def __init__(self, capacity):
        self.capacity = capacity
        self.mails = {}
        self.order = []
        self.dropped = 0

    def deliver(self, mail_id, expire_ms, attachments):
        if mail_id in self.mails:
            del self.mails[mail_id]
            self.order.remove(mail_id)
        self.mails[mail_id] = [expire_ms, list(attachments), False]
        self.order.append(mail_id)
        while len(self.order) > self.capacity:
            oldest = self.order.pop(0)
            del self.mails[oldest]
            self.dropped += 1

    def claim(self, mail_id, now_ms):
        if mail_id not in self.mails:
            raise MailError("missing")
        expire_ms, attachments, claimed = self.mails[mail_id]
        if now_ms >= expire_ms:
            raise MailError("expired")
        if claimed:
            return []
        self.mails[mail_id][2] = True
        return list(attachments)

    def purge(self, now_ms):
        expired = [mail_id for mail_id in self.order
                   if self.mails[mail_id][0] <= now_ms]
        for mail_id in expired:
            del self.mails[mail_id]
            self.order.remove(mail_id)
        return len(expired)

    def size(self, now_ms):
        return sum(1 for mail_id in self.order
                   if self.mails[mail_id][0] > now_ms)


class DifferentialTest(unittest.TestCase):
    def test_matches_reference_over_random_sequences(self):
        rng = random.Random(20260930)
        for trial in range(200):
            capacity = rng.randint(1, 6)
            inbox = Inbox(capacity=capacity)
            ref = RefInbox(capacity=capacity)
            ids = ["t%d-m%d" % (trial, index) for index in range(6)]
            for _step in range(rng.randint(10, 40)):
                now = rng.randint(0, 300)
                action = rng.choice(
                    ("deliver", "deliver", "claim", "claim", "purge", "size"))
                if action == "deliver":
                    mail_id = rng.choice(ids)
                    expire_ms = rng.randint(0, 300)
                    attachments = [("a%d" % index, rng.randint(1, 3))
                                   for index in range(rng.randint(0, 3))]
                    inbox.deliver(Mail(mail_id, expire_ms, attachments))
                    ref.deliver(mail_id, expire_ms, attachments)
                elif action == "claim":
                    mail_id = rng.choice(ids + ["ghost"])
                    try:
                        got = inbox.claim(mail_id, now)
                        got_error = False
                    except MailError:
                        got = []
                        got_error = True
                    try:
                        want = ref.claim(mail_id, now)
                        want_error = False
                    except MailError:
                        want = []
                        want_error = True
                    self.assertEqual(got_error, want_error,
                                     (trial, action, mail_id, now))
                    self.assertEqual(got, want,
                                     (trial, action, mail_id, now))
                elif action == "purge":
                    self.assertEqual(inbox.purge(now), ref.purge(now),
                                     (trial, "purge", now))
                self.assertEqual(inbox.size(now), ref.size(now),
                                 (trial, "size", now))
                self.assertEqual(inbox.dropped, ref.dropped,
                                 (trial, "dropped"))


if __name__ == "__main__":
    unittest.main()
