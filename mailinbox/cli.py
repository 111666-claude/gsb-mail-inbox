"""命令行入口：跑邮件样例。"""

import argparse

from .core import Inbox, Mail


def build_parser():
    parser = argparse.ArgumentParser(prog="mail-inbox", description="邮件与附件")
    parser.add_argument("--sample", default="claim", help="claim / expire / capacity / scan")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.sample == "claim":
        inbox = Inbox()
        inbox.deliver(Mail("m-1", 60000, [("gold", 3)]))
        granted = 0
        first = inbox.claim("m-1", 1000)
        granted += sum(count for _, count in first)
        second = inbox.claim("m-1", 2000)
        granted += sum(count for _, count in second)
        print("first=%s second=%s items=%d" % (
            "true" if first else "false",
            "true" if second else "false",
            granted))
    elif args.sample == "expire":
        inbox = Inbox()
        inbox.deliver(Mail("m-1", 1000, [("gold", 1)]))
        print("purged=%d size=%d" % (inbox.purge(1000), inbox.size(1000)))
    elif args.sample == "capacity":
        inbox = Inbox(capacity=2)
        for index in range(5):
            inbox.deliver(Mail("m-%d" % index, 60000, []))
        print("size=%d dropped=%d" % (inbox.size(0), inbox.dropped))
    elif args.sample == "scan":
        inbox = Inbox(capacity=100000)
        for index in range(2000):
            inbox.deliver(Mail("m-%d" % index, 600000, []))
        for _ in range(2000):
            inbox.claim("m-1999", 0)
        print("scanned=%d" % inbox.scanned_count())
    else:
        raise SystemExit("需要 --sample claim|expire|capacity|scan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
