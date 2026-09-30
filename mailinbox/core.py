"""邮件与附件：容量上限、过期回收、附件领取幂等。

缺陷：重复领取会重复发附件、过期邮件不回收、容量上限不生效也不记账、每次操作全表扫描。
"""


class MailError(ValueError):
    """邮件操作不合法。"""


class Mail:
    """一封带附件的邮件。"""

    def __init__(self, mail_id, expire_ms, attachments=()):
        self.mail_id = mail_id
        self.expire_ms = expire_ms
        self.attachments = list(attachments)
        self.claimed = False


class Inbox:
    """收件箱。"""

    def __init__(self, capacity=10):
        self.capacity = capacity
        self.mails = []
        self.dropped = 0
        self.scanned = 0

    def deliver(self, mail):
        """投递一封邮件。缺陷：不看容量、不淘汰最旧的、也不累加 dropped。"""
        self.mails.append(mail)

    def expired(self, mail, now_ms):
        """判断邮件是否过期。缺陷：边界用严格小于，到点的那一封还不算过期。"""
        return now_ms < mail.expire_ms

    def purge(self, now_ms):
        """回收过期邮件并返回清理数量。缺陷：什么都不清理。"""
        self.scanned += len(self.mails)
        return 0

    def claim(self, mail_id, now_ms):
        """领取附件。缺陷：不检查是不是已经领过，重复领取会重复发附件。"""
        for mail in self.mails:
            self.scanned += 1
            if mail.mail_id == mail_id:
                return list(mail.attachments)
        raise MailError("没有这封邮件：%s" % mail_id)

    def size(self, now_ms):
        """当前邮件数。缺陷：过期的也算在里面，而且每次全表扫描。"""
        self.scanned += len(self.mails)
        return len(self.mails)

    def scanned_count(self):
        return self.scanned
