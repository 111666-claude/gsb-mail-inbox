"""邮件与附件：容量上限、过期回收、附件领取幂等。

索引结构（全程不全表扫描）：

- ``_records``：按 mail_id 索引，claim / deliver / 淘汰均为 O(1)。
- ``_expire_heap``：按 (expire_ms, seq) 排序的最小堆，供 purge 与 size 定位
  到期邮件；过期堆条目惰性失效，被淘汰或重复投递的旧条目只在弹出时丢弃。
- ``_due`` / ``_due_fenwick``：已经确认到期、但还没被 purge 回收的邮件，
  按到期时刻有序，配合 Fenwick 树让 size 用 O(log N) 二分出「已到期但未回收」
  的数量，且不修改收件箱（非单调的 now_ms 也能正确统计）。
- ``_order``：投递顺序的 deque，容量超限时淘汰最旧的未回收邮件。
"""

from collections import deque
import heapq


class MailError(ValueError):
    """邮件操作不合法。"""


class Mail:
    """一封带附件的邮件。"""

    def __init__(self, mail_id, expire_ms, attachments=()):
        self.mail_id = mail_id
        self.expire_ms = expire_ms
        self.attachments = list(attachments)
        self.claimed = False


class _Record:
    """收件箱内部的邮件记录，挂各类索引指针。"""

    __slots__ = ("mail_id", "expire_ms", "attachments", "claimed", "seq")

    def __init__(self, mail, seq):
        self.mail_id = mail.mail_id
        self.expire_ms = mail.expire_ms
        self.attachments = mail.attachments
        self.claimed = mail.claimed
        self.seq = seq


class _Fenwick:
    """一维 Fenwick 树，记录有序位置上的 0/1，前缀和用于二分计数。"""

    __slots__ = ("_tree",)

    def __init__(self):
        self._tree = [0]

    def add(self, index, delta):
        tree = self._tree
        if index >= len(tree):
            tree.extend([0] * (index + 1 - len(tree)))
        while index < len(tree):
            tree[index] += delta
            index += index & -index

    def prefix_sum(self, index):
        """前 index 个位置（1-based，闭区间）的和。"""
        tree = self._tree
        total = 0
        if index >= len(tree):
            index = len(tree) - 1
        while index > 0:
            total += tree[index]
            index -= index & -index
        return total


class Inbox:
    """收件箱。"""

    def __init__(self, capacity=10):
        self.capacity = capacity
        self.dropped = 0
        self.scanned = 0
        self._records = {}
        self._order = deque()
        self._expire_heap = []
        self._due = []
        self._due_fenwick = _Fenwick()
        self._due_purged_len = 0
        self._seq = 0

    def deliver(self, mail):
        """投递一封邮件；超过容量时淘汰最旧的一封并累加 dropped。

        同一 mail_id 再次投递时，旧邮件从所有索引中摘除（惰性的堆条目会在
        弹出时按 seq 识别为失效），不计淘汰。
        """
        if mail.mail_id in self._records:
            self._remove(mail.mail_id)
        seq = self._seq
        self._seq += 1
        record = _Record(mail, seq)
        self._records[mail.mail_id] = record
        self._order.append(mail.mail_id)
        heapq.heappush(self._expire_heap, (mail.expire_ms, seq, mail.mail_id))
        while len(self._records) > self.capacity:
            self._evict_oldest()

    def _evict_oldest(self):
        """淘汰投递顺序最旧的、仍在收件箱中的一封。"""
        while self._order:
            mail_id = self._order.popleft()
            record = self._records.pop(mail_id, None)
            if record is not None:
                self._drop_due(mail_id)
                self.dropped += 1
                return

    def _remove(self, mail_id):
        """从各索引摘除一封邮件（不记 dropped，用于重复投递）。"""
        record = self._records.pop(mail_id)
        try:
            self._order.remove(mail_id)
        except ValueError:
            pass
        self._drop_due(mail_id)

    def _drop_due(self, mail_id):
        """若邮件已进入「到期未回收」表，把它从有序表与 Fenwick 树中删掉。"""
        due = self._due
        lo, hi = self._due_purged_len, len(due)
        while lo < hi:
            mid = (lo + hi) // 2
            if due[mid][1] == mail_id:
                due.pop(mid)
                self._due_fenwick.add(mid + 1, -1)
                return
            if due[mid][1] < mail_id:
                lo = mid + 1
            else:
                hi = mid

    def _drain(self, now_ms):
        """把 expire_ms <= now_ms 的在册邮件从过期堆移入到期表。

        过期堆里失效的旧条目（已淘汰 / 已回收 / 被重复投递顶替）直接丢弃。
        """
        heap = self._expire_heap
        due = self._due
        while heap and heap[0][0] <= now_ms:
            expire_ms, seq, mail_id = heapq.heappop(heap)
            self.scanned += 1
            record = self._records.get(mail_id)
            if record is None or record.seq != seq or record.expire_ms != expire_ms:
                continue
            pos = len(due)
            due.append((expire_ms, mail_id))
            self._due_fenwick.add(pos + 1, 1)

    def _due_count_at(self, now_ms):
        """到期表中 expire_ms <= now_ms 的条数（Fenwick + 二分，不改状态）。"""
        due = self._due
        lo, hi = 0, len(due)
        while lo < hi:
            mid = (lo + hi) // 2
            if due[mid][0] <= now_ms:
                lo = mid + 1
            else:
                hi = mid
        return self._due_fenwick.prefix_sum(lo)

    def expired(self, mail, now_ms):
        """now_ms >= expire_ms 即过期（到点就算）。"""
        return now_ms >= mail.expire_ms

    def purge(self, now_ms):
        """回收 expire_ms <= now_ms 的邮件，返回实际清理数量。"""
        self.scanned += 1
        self._drain(now_ms)
        due = self._due
        end = 0
        while end < len(due) and due[end][0] <= now_ms:
            end += 1
        count = 0
        for i in range(self._due_purged_len, end):
            mail_id = due[i][1]
            record = self._records.pop(mail_id, None)
            if record is not None:
                count += 1
        self._due_purged_len = end
        if end and end * 2 >= len(due):
            del due[:end]
            self._due_purged_len = 0
        return count

    def claim(self, mail_id, now_ms):
        """领取附件。

        首次领取且未过期时返回附件列表；同一封邮件第二次领取返回 ``False``。
        不存在（已回收 / 已淘汰 / 从未投递）或已过期时抛 :class:`MailError`。
        """
        self.scanned += 1
        record = self._records.get(mail_id)
        if record is None:
            raise MailError("没有这封邮件：%s" % mail_id)
        if now_ms >= record.expire_ms:
            raise MailError("邮件已过期：%s" % mail_id)
        if record.claimed:
            return False
        record.claimed = True
        return list(record.attachments)

    def size(self, now_ms):
        """当前未过期邮件数（过期但尚未 purge 的不计）。"""
        self.scanned += 1
        self._drain(now_ms)
        return len(self._records) - self._due_count_at(now_ms)

    def scanned_count(self):
        return self.scanned
