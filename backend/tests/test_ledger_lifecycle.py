"""End-to-end lifecycle test for the bi-temporal receipt rules.

Stays in the standard library: it drives the domain ledger plus the
``DeliveryMoment`` interception helper through the full business story, so it
runs without the FastAPI/SQLAlchemy stack.
"""

import unittest
from datetime import datetime, timedelta, timezone

from domain.ledger import (
    BatchStatus,
    DeliveryMoment,
    EditionState,
    EntryKind,
    LedgerBatch,
    LedgerEntry,
    RejectReason,
    apply_correction,
    classify,
    replay,
    validate_batch,
    visible_receipt,
)


UTC = timezone.utc
KEY = 'NY-HALL/2026-11-01/matinee'
DUE = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
GRACE = 24


def at(text):
    return datetime.fromisoformat(text)


def r(seq, posted, received, ref):
    return LedgerEntry(KEY, EntryKind.receipt, seq, at(posted), at(received), ref)


def w(seq, posted):
    return LedgerEntry(KEY, EntryKind.withdrawal, seq, at(posted))


class LifecycleTests(unittest.TestCase):
    def test_full_backfill_withdrawal_correction_delivery_story(self):
        batches = []
        deliveries: dict[str, list[DeliveryMoment]] = {}

        # 1) 补录倒填：11-01 10:00 入账，收到时间倒填为当天 06:45。
        first = LedgerBatch('b1', at('2026-11-01T10:00:00+00:00'),
                            (r(0, '2026-11-01T10:00:00+00:00',
                               '2026-11-01T06:45:00+00:00', 'NY-1101.csv'),))
        self.assertIsNone(validate_batch(first.entries))
        batches.append(first)

        # 晨报按当时掌握重算：入账前不可见 -> 缺报（检查点取窗口内时刻）。
        before = replay(batches, at('2026-11-01T09:00:00+00:00'))
        self.assertEqual(classify(visible_receipt(before.get(KEY),
                                                   at('2026-11-01T09:00:00+00:00')),
                                  DUE, at('2026-11-01T09:00:00+00:00'), GRACE),
                         'waiting_input')

        # 入账后可见；按收到时间（而非入账时间）判迟报 -> backfill。
        after = replay(batches, at('2026-11-01T10:00:00+00:00'))
        receipt = visible_receipt(after[KEY], at('2026-11-01T10:00:00+00:00'))
        self.assertEqual(receipt, at('2026-11-01T06:45:00+00:00'))
        self.assertEqual(classify(receipt, DUE, at('2026-11-01T10:00:00+00:00'), GRACE),
                         'backfill')

        # 2) 主管批准，排队一版晨报投递。
        deliveries[KEY] = [DeliveryMoment('del-1', KEY, at('2026-11-01T10:05:00+00:00'))]

        # 3) 同场同入账时刻内容冲突 -> 整批不入账。
        conflict_entries = (
            r(0, '2026-11-01T11:00:00+00:00', '2026-11-01T06:45:00+00:00', 'a.csv'),
            r(0, '2026-11-01T11:00:00+00:00', '2026-11-01T06:50:00+00:00', 'b.csv'),
        )
        self.assertEqual(validate_batch(conflict_entries),
                         RejectReason.same_instant_conflict)
        rejected = LedgerBatch('bad', at('2026-11-01T11:00:00+00:00'),
                               conflict_entries, BatchStatus.rejected,
                               RejectReason.same_instant_conflict.value)
        batches.append(rejected)
        # 被拒批次对重放完全不可见。
        states = replay(batches, at('2026-11-01T12:00:00+00:00'))
        self.assertEqual(states[KEY].batch_id, 'b1')

        # 4) 更正先于投递：拦下已排队旧晨报，按新版补发。
        correction = LedgerBatch('b2', at('2026-11-01T13:00:00+00:00'),
                                 (r(0, '2026-11-01T13:00:00+00:00',
                                    '2026-11-01T06:20:00+00:00', 'NY-1101-v2.csv'),))
        batches.append(correction)
        new_state = replay(batches, at('2026-11-01T13:00:00+00:00'))[KEY]
        self.assertEqual(new_state.source_ref, 'NY-1101-v2.csv')
        outcome = apply_correction(KEY, new_state, deliveries,
                                   now=at('2026-11-01T13:00:00+00:00'), eligible=True)
        self.assertEqual(outcome['superseded'], ['del-1'])
        self.assertTrue(outcome['reissued_delivery_id'].endswith('v2'))
        self.assertEqual(len(deliveries[KEY]), 2)
        self.assertIsNotNone(deliveries[KEY][0].superseded_at)
        self.assertTrue(deliveries[KEY][1].is_pending)

        # 5) 撤回按缺报：撤回事件生效后无有效回执（仍在补发窗口内）。
        cancel = LedgerBatch('b3', at('2026-11-01T15:00:00+00:00'),
                             (w(0, '2026-11-01T15:00:00+00:00'),))
        batches.append(cancel)
        withdrawn = replay(batches, at('2026-11-01T15:00:00+00:00'))[KEY]
        self.assertIsNone(visible_receipt(withdrawn, at('2026-11-01T15:00:00+00:00')))
        self.assertEqual(
            classify(visible_receipt(withdrawn, at('2026-11-01T15:00:00+00:00')),
                     DUE, at('2026-11-01T15:00:00+00:00'), GRACE),
            'waiting_input')

    def test_delivery_first_then_correction_is_idempotent_and_evidence_kept(self):
        batches = []
        # 初版回执入账并投递，投递先完成。
        first = LedgerBatch('b1', at('2026-11-02T08:00:00+00:00'),
                            (r(0, '2026-11-02T08:00:00+00:00',
                               '2026-11-01T06:45:00+00:00', 'v1.csv'),))
        batches.append(first)
        sent = DeliveryMoment('del-1', KEY, at('2026-11-02T08:05:00+00:00'),
                              delivered_at=at('2026-11-02T08:10:00+00:00'),
                              plan_snapshot={'source_ref': 'v1.csv'})
        deliveries = {KEY: [sent]}

        # 更正后到。
        correction = LedgerBatch('b2', at('2026-11-02T09:00:00+00:00'),
                                 (r(0, '2026-11-02T09:00:00+00:00',
                                    '2026-11-01T06:20:00+00:00', 'v2.csv'),))
        batches.append(correction)
        state = replay(batches, at('2026-11-02T09:00:00+00:00'))[KEY]
        outcome = apply_correction(KEY, state, deliveries,
                                   now=at('2026-11-02T09:00:00+00:00'), eligible=True)

        # 查得回原回执与判定（快照保留），重跑不重复投递。
        self.assertTrue(outcome['already_delivered'])
        self.assertEqual(outcome['active_delivery_id'], 'del-1')
        self.assertIsNone(outcome['reissued_delivery_id'])
        self.assertEqual(len(deliveries[KEY]), 1)
        self.assertEqual(deliveries[KEY][0].plan_snapshot['source_ref'], 'v1.csv')

        # 再次"释放同一 approved 报告"仍返回同一投递编号（幂等）。
        rerun = apply_correction(KEY, state, deliveries,
                                 now=at('2026-11-02T09:30:00+00:00'), eligible=True)
        self.assertEqual(rerun['active_delivery_id'], 'del-1')
        self.assertEqual(len(deliveries[KEY]), 1)

    def test_back_dated_receipt_inside_window_but_booked_after_deadline(self):
        # 收到时间在窗口内（06:45，截止次日 06:30），但入账拖到窗口关闭后。
        # 用 as_of=入账时刻重算时整体已 expired；用更早 as_of 重算则是缺报。
        late_book = LedgerBatch('b1', at('2026-11-03T00:00:00+00:00'),
                                (r(0, '2026-11-03T00:00:00+00:00',
                                   '2026-11-01T06:45:00+00:00', 'late.csv'),))
        batches = [late_book]
        # 窗口关闭前重放：凭证尚不可见 -> 缺报（当时掌握的情况）。
        pre = replay(batches, at('2026-11-02T06:00:00+00:00'))
        self.assertNotIn(KEY, pre)
        # 入账后重放：凭证可见，但 as_of 已过 deadline -> expired。
        post = replay(batches, at('2026-11-03T00:00:00+00:00'))
        receipt = visible_receipt(post[KEY], at('2026-11-03T00:00:00+00:00'))
        self.assertEqual(classify(receipt, DUE, at('2026-11-03T00:00:00+00:00'), GRACE),
                         'expired')


if __name__ == '__main__':
    unittest.main()
