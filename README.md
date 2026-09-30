# mail-inbox

邮件与附件：容量上限、过期回收、附件领取幂等，只用 Python 标准库。

```
python3 -m mailinbox.cli --sample claim
python3 -m mailinbox.cli --sample expire
python3 -m mailinbox.cli --sample capacity
python3 -m unittest discover -s tests -v
```

## 口径（README 为准）

- **领取幂等**：同一封邮件的附件只能发一次；第二次领取返回空结果（`False`），不再重发附件。
  已过期或不存在时抛 `MailError`。
- **过期按时刻**：`now_ms >= expire_ms` 就算过期；`purge(now_ms)` 回收过期邮件并返回清理数量，
  `size(now_ms)` 只数没过期的。
- **容量上限**：收件箱最多保留 `capacity` 封，超出时淘汰最旧的一封并把 `dropped` 加一。
- **查询代价**：`claim`、`purge`、`size` 都不许每次全表扫描，`scanned_count()` 不随邮件数线性增长。
- **规模**：单账号 50 万封邮件、每秒 10 万次领取与查询，内存 O(邮件数)。

## 输出契约（不改格式）

```
first=true second=false items=3
purged=1 size=0
size=2 dropped=3
scanned<=4000
```

## 目录

```
mailinbox/core.py   邮件、过期与容量
mailinbox/cli.py    命令行入口
tests/              unittest 用例
```
