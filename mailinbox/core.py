"""邮件与附件：容量上限、过期回收、附件领取幂等。

口径（与 README 一致）：
- 领取幂等：同一封邮件的附件只发一次，重复领取返回空列表；不存在或已过期抛 MailError。
- 过期按时刻：now_ms >= expire_ms 即过期；purge 回收并返回清理数量；size 只数未过期的。
- 容量上限：最多保留 capacity 封，超出淘汰最旧一封并把 dropped 加一。
- 查询代价：claim 走字典索引 O(1)；purge/size 借助到期最小堆与有序待清表，均摊
  O(log n) 单封邮件只被触碰一次，绝不全表扫描。
"""

import bisect
import heapq
from collections import deque


class MailError(ValueError):
    """邮件操作不合法。"""


class Mail:
    """一封带附件的邮件。"""

    def __init__(self, mail_id, expire_ms, attachments=()):
        self.mail_id = mail_id
        self.expire_ms = expire_ms
        self.attachments = list(attachments)
        self.claimed = False


_INF = float("inf")


class Inbox:
    """收件箱。

    索引结构（内存 O(邮件数)）：
    - _mails / _seqs：mail_id -> 邮件 / 当前投递序号，claim 按 id 直取，O(1)。
    - _order：投递顺序队列 (seq, mail_id)，懒删除，容量超限时淘汰最旧一封。
    - _expiry：(expire_ms, seq, mail_id) 最小堆，懒删除，按到期时间取过期邮件。
    - _pending：已确认过期、等待 purge 回收的有序表，size/purge 用二分计数。
    """

    def __init__(self, capacity=10):
        self.capacity = capacity
        self.dropped = 0
        self.scanned = 0
        self._mails = {}
        self._seqs = {}
        self._next_seq = 0
        self._order = deque()
        self._expiry = []
        self._pending = []

    def _sift_expired(self, now_ms):
        """把 expire_ms <= now_ms 的邮件从到期堆挪进待清表。

        堆是懒删除的：条目可能对应已被容量淘汰或已被回收的邮件，用投递序号
        判重跳过。每封邮件至多被挪一次。
        """
        expiry = self._expiry
        pending = self._pending
        seqs = self._seqs
        while expiry and expiry[0][0] <= now_ms:
            expire_ms, seq, mail_id = heapq.heappop(expiry)
            self.scanned += 1
            if seqs.get(mail_id) != seq:
                continue
            bisect.insort(pending, (expire_ms, seq, mail_id))

    def _remove(self, mail):
        """从收件箱彻底移除一封邮件（不记 dropped），各索引同步清理。"""
        seq = self._seqs.pop(mail.mail_id)
        self._mails.pop(mail.mail_id, None)
        entry = (mail.expire_ms, seq, mail.mail_id)
        index = bisect.bisect_left(self._pending, entry)
        if index < len(self._pending) and self._pending[index] == entry:
            del self._pending[index]

    def _evict_oldest(self):
        """淘汰最旧一封；返回 False 表示没有可淘汰的邮件。"""
        order = self._order
        seqs = self._seqs
        while order:
            seq, mail_id = order.popleft()
            self.scanned += 1
            if seqs.get(mail_id) != seq:
                continue
            self._remove(self._mails[mail_id])
            self.dropped += 1
            return True
        return False

    def deliver(self, mail):
        """投递一封邮件；超过容量时淘汰最旧一封并把 dropped 加一。"""
        existing = self._mails.get(mail.mail_id)
        if existing is not None:
            self._remove(existing)
        seq = self._next_seq
        self._next_seq += 1
        self._mails[mail.mail_id] = mail
        self._seqs[mail.mail_id] = seq
        self._order.append((seq, mail.mail_id))
        heapq.heappush(self._expiry, (mail.expire_ms, seq, mail.mail_id))
        while len(self._mails) > self.capacity:
            if not self._evict_oldest():
                break

    def expired(self, mail, now_ms):
        """now_ms 不小于 expire_ms 即过期（到点就算）。"""
        return now_ms >= mail.expire_ms

    def purge(self, now_ms):
        """回收所有 expire_ms <= now_ms 的邮件，返回清理数量。"""
        self._sift_expired(now_ms)
        boundary = (now_ms, _INF, None)
        count = bisect.bisect_right(self._pending, boundary)
        if count:
            expired = self._pending[:count]
            del self._pending[:count]
            for expire_ms, seq, mail_id in expired:
                del self._mails[mail_id]
                del self._seqs[mail_id]
        return count

    def claim(self, mail_id, now_ms):
        """领取附件。

        - 不存在（已被回收或淘汰）或已过期：抛 MailError。
        - 同一封邮件第二次领取：返回空列表（falsy），不重发附件。
        """
        mail = self._mails.get(mail_id)
        if mail is None:
            raise MailError("没有这封邮件：%s" % mail_id)
        self.scanned += 1
        if self.expired(mail, now_ms):
            raise MailError("邮件已过期：%s" % mail_id)
        if mail.claimed:
            return []
        mail.claimed = True
        return list(mail.attachments)

    def size(self, now_ms):
        """未过期邮件数。"""
        self._sift_expired(now_ms)
        boundary = (now_ms, _INF, None)
        expired = bisect.bisect_right(self._pending, boundary)
        return len(self._mails) - expired

    def scanned_count(self):
        return self.scanned
