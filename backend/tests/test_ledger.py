import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from domain.ledger import (
    BatchConflict,
    BatchStatus,
    DeliveryMoment,
    EditionState,
    EntryKind,
    LedgerBatch,
    LedgerEntry,
    RejectReason,
    apply_correction,
    classify,
    find_same_instant_conflicts,
    replay,
    validate_batch,
    visible_receipt,
)


UTC = timezone.utc
NY = ZoneInfo('America/New_York')


def dt(value):
    return datetime.fromisoformat(value)


def receipt(edition, seq, posted_at, received_at, source_ref):
    return LedgerEntry(edition, EntryKind.receipt, seq, dt(posted_at), dt(received_at), source_ref)


def withdrawal(edition, seq, posted_at):
    return LedgerEntry(edition, EntryKind.withdrawal, seq, dt(posted_at))


def batch(batch_id, posted_at, entries, status=BatchStatus.accepted, reason=None):
    return LedgerBatch(batch_id, dt(posted_at), tuple(entries), status, reason)


class BatchValidationTests(unittest.TestCase):
    def test_empty_batch_is_rejected(self):
        self.assertEqual(validate_batch([]), RejectReason.empty_batch)

    def test_same_instant_conflicting_payloads_reject_whole_batch(self):
        entries = (
            receipt('NY/2026-11-01/matinee', 0, '2026-11-02T08:00:00+00:00',
                    '2026-11-01T06:45:00+00:00', 'a.csv'),
            receipt('NY/2026-11-01/matinee', 0, '2026-11-02T08:00:00+00:00',
                    '2026-11-01T07:10:00+00:00', 'b.csv'),
        )
        self.assertEqual(find_same_instant_conflicts(entries),
                         [BatchConflict('NY/2026-11-01/matinee', 0)])
        self.assertEqual(validate_batch(entries), RejectReason.same_instant_conflict)

    def test_same_instant_identical_payloads_are_idempotent(self):
        entries = (
            receipt('NY/2026-11-01/matinee', 0, '2026-11-02T08:00:00+00:00',
                    '2026-11-01T06:45:00+00:00', 'a.csv'),
            receipt('NY/2026-11-01/matinee', 0, '2026-11-02T08:00:00+00:00',
                    '2026-11-01T06:45:00+00:00', 'a.csv'),
        )
        self.assertEqual(find_same_instant_conflicts(entries), [])
        self.assertIsNone(validate_batch(entries))

    def test_different_seqs_at_same_instant_are_not_conflicts(self):
        entries = (
            receipt('NY/2026-11-01/matinee', 0, '2026-11-02T08:00:00+00:00',
                    '2026-11-01T06:45:00+00:00', 'a.csv'),
            receipt('NY/2026-11-01/matinee', 1, '2026-11-02T08:00:00+00:00',
                    '2026-11-01T06:45:00+00:00', 'a.csv'),
        )
        self.assertEqual(find_same_instant_conflicts(entries), [])

    def test_different_editions_at_same_instant_are_independent(self):
        entries = (
            receipt('NY/2026-11-01/matinee', 0, '2026-11-02T08:00:00+00:00',
                    '2026-11-01T06:45:00+00:00', 'a.csv'),
            receipt('LDN/2026-11-01/matinee', 0, '2026-11-02T08:00:00+00:00',
                    '2026-11-01T07:20:00+00:00', 'b.csv'),
        )
        self.assertIsNone(validate_batch(entries))

    def test_withdrawal_cannot_carry_payload(self):
        with self.assertRaises(ValueError):
            LedgerEntry('e', EntryKind.withdrawal, 0, dt('2026-11-01T08:00:00+00:00'),
                        dt('2026-11-01T08:00:00+00:00'), 'x.csv')


class ReplayTests(unittest.TestCase):
    def setUp(self):
        # Back-dated receipt: booked on the 2nd but said to have arrived on the 1st.
        self.first = batch('b1', '2026-11-02T08:00:00+00:00', [
            receipt('NY/2026-11-01/matinee', 0, '2026-11-02T08:00:00+00:00',
                    '2026-11-01T06:45:00+00:00', 'NY-1101.csv'),
        ])

    def test_replay_only_uses_batches_visible_at_as_of(self):
        states = replay([self.first], dt('2026-11-02T07:59:59+00:00'))
        self.assertEqual(states, {})
        states = replay([self.first], dt('2026-11-02T08:00:00+00:00'))
        self.assertIn('NY/2026-11-01/matinee', states)
        self.assertTrue(states['NY/2026-11-01/matinee'].has_receipt)

    def test_replay_takes_last_entry_by_posted_time(self):
        correction = batch('b2', '2026-11-03T09:00:00+00:00', [
            receipt('NY/2026-11-01/matinee', 0, '2026-11-03T09:00:00+00:00',
                    '2026-11-01T06:30:00+00:00', 'NY-1101-v2.csv'),
        ])
        states = replay([self.first, correction], dt('2026-11-03T09:00:00+00:00'))
        state = states['NY/2026-11-01/matinee']
        self.assertEqual(state.source_ref, 'NY-1101-v2.csv')
        self.assertEqual(state.received_at, dt('2026-11-01T06:30:00+00:00'))
        self.assertEqual(state.batch_id, 'b2')
        # An earlier replay still sees the old version.
        old = replay([self.first, correction], dt('2026-11-03T08:59:59+00:00'))
        self.assertEqual(old['NY/2026-11-01/matinee'].source_ref, 'NY-1101.csv')

    def test_withdrawal_leaves_edition_without_receipt(self):
        cancel = batch('b2', '2026-11-03T09:00:00+00:00', [
            withdrawal('NY/2026-11-01/matinee', 0, '2026-11-03T09:00:00+00:00'),
        ])
        states = replay([self.first, cancel], dt('2026-11-03T09:00:00+00:00'))
        state = states['NY/2026-11-01/matinee']
        self.assertFalse(state.has_receipt)
        self.assertIsNone(visible_receipt(state, dt('2026-11-03T09:00:00+00:00')))

    def test_rejected_batch_is_invisible_to_replay(self):
        bad = batch('bad', '2026-11-02T09:00:00+00:00', [
            receipt('NY/2026-11-01/matinee', 0, '2026-11-02T09:00:00+00:00',
                    '2026-11-01T06:45:00+00:00', 'x.csv'),
        ], status=BatchStatus.rejected, reason=RejectReason.same_instant_conflict.value)
        states = replay([bad, self.first], dt('2026-11-03T00:00:00+00:00'))
        self.assertEqual(states['NY/2026-11-01/matinee'].batch_id, 'b1')

    def test_missing_edition_means_missing_report(self):
        states = replay([self.first], dt('2026-11-03T00:00:00+00:00'))
        self.assertIsNone(visible_receipt(states.get('MAD/2026-11-01/evening'),
                                          dt('2026-11-03T00:00:00+00:00')))


class LateClassificationTests(unittest.TestCase):
    def setUp(self):
        # NY 2026-11-01 01:30 "later" fold -> 06:30 UTC (as in BUSINESS.md).
        self.due = datetime(2026, 11, 1, 6, 30, tzinfo=UTC)
        # Inside the backfill window (deadline is due + 24 h).
        self.as_of = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def test_back_dated_receipt_judged_on_received_time_is_backfill(self):
        # Booked late, received at 06:45 UTC -> backfill even though the
        # booking happened more than a day after the deadline.
        status = classify(dt('2026-11-01T06:45:00+00:00'), self.due, self.as_of, 24)
        self.assertEqual(status, 'backfill')

    def test_received_before_due_is_on_time(self):
        status = classify(dt('2026-11-01T06:00:00+00:00'), self.due, self.as_of, 24)
        self.assertEqual(status, 'on_time')

    def test_withdrawal_classifies_as_missing_report(self):
        state = EditionState('e', EntryKind.withdrawal)
        received = visible_receipt(state, self.as_of)
        self.assertEqual(classify(received, self.due, self.as_of, 24), 'waiting_input')

    def test_receipt_booked_after_as_of_is_not_visible(self):
        state = EditionState(
            'e', EntryKind.receipt,
            received_at=dt('2026-11-01T06:00:00+00:00'), source_ref='x.csv',
            posted_at=dt('2026-11-03T00:00:00+00:00'))
        received = visible_receipt(state, dt('2026-11-01T11:00:00+00:00'))
        self.assertIsNone(received)
        self.assertEqual(classify(received, self.due,
                                  dt('2026-11-01T11:00:00+00:00'), 24), 'waiting_input')

    def test_receipt_at_or_after_deadline_expires(self):
        deadline_receipt = self.due + timedelta(hours=24)
        status = classify(deadline_receipt, self.due,
                          self.due + timedelta(hours=25), 24)
        self.assertEqual(status, 'expired')

    def test_stale_receipt(self):
        stale = self.due - timedelta(hours=49)
        status = classify(stale, self.due, self.as_of, 24)
        self.assertEqual(status, 'stale_input')


class CorrectionDeliveryRaceTests(unittest.TestCase):
    KEY = 'NY/2026-11-01/matinee'

    def _queued(self, delivery_id):
        return DeliveryMoment(delivery_id, self.KEY,
                              scheduled_at=dt('2026-11-01T05:00:00+00:00'))

    def test_correction_first_intercepts_queued_report_and_reissues(self):
        deliveries = {self.KEY: [self._queued('del-1')]}
        state = EditionState(self.KEY, EntryKind.receipt,
                             received_at=dt('2026-11-01T06:45:00+00:00'),
                             source_ref='v2.csv',
                             posted_at=dt('2026-11-01T05:30:00+00:00'), batch_id='b2')
        outcome = apply_correction(self.KEY, state, deliveries,
                                   now=dt('2026-11-01T05:30:00+00:00'), eligible=True)
        self.assertEqual(outcome['superseded'], ['del-1'])
        self.assertEqual(outcome['reissued_delivery_id'], f'del-{self.KEY}-v2')
        self.assertIsNotNone(deliveries[self.KEY][0].superseded_at)
        self.assertTrue(deliveries[self.KEY][1].is_pending)
        self.assertEqual(deliveries[self.KEY][1].replaces, 'del-1')
        # The reissued delivery carries the new version's snapshot.
        self.assertEqual(deliveries[self.KEY][1].plan_snapshot['source_ref'], 'v2.csv')

    def test_withdrawal_cancels_queued_report_without_reissue(self):
        deliveries = {self.KEY: [self._queued('del-1')]}
        state = EditionState(self.KEY, EntryKind.withdrawal,
                             posted_at=dt('2026-11-01T05:30:00+00:00'), batch_id='b2')
        outcome = apply_correction(self.KEY, state, deliveries,
                                   now=dt('2026-11-01T05:30:00+00:00'), eligible=False)
        self.assertEqual(outcome['superseded'], ['del-1'])
        self.assertIsNone(outcome['reissued_delivery_id'])
        self.assertIsNone(outcome['active_delivery_id'])

    def test_ineligible_correction_intercepts_but_does_not_reissue(self):
        deliveries = {self.KEY: [self._queued('del-1')]}
        state = EditionState(self.KEY, EntryKind.receipt,
                             received_at=dt('2026-11-01T06:45:00+00:00'),
                             source_ref='late.csv',
                             posted_at=dt('2026-11-03T00:00:00+00:00'), batch_id='b2')
        outcome = apply_correction(self.KEY, state, deliveries,
                                   now=dt('2026-11-03T00:00:00+00:00'), eligible=False)
        self.assertEqual(outcome['superseded'], ['del-1'])
        self.assertIsNone(outcome['reissued_delivery_id'])

    def test_delivery_first_keeps_evidence_and_rerun_is_idempotent(self):
        sent = DeliveryMoment('del-1', self.KEY,
                              scheduled_at=dt('2026-11-01T05:00:00+00:00'),
                              delivered_at=dt('2026-11-01T06:30:00+00:00'),
                              plan_snapshot={'source_ref': 'v1.csv'})
        deliveries = {self.KEY: [sent]}
        state = EditionState(self.KEY, EntryKind.receipt,
                             received_at=dt('2026-11-01T06:45:00+00:00'),
                             source_ref='v2.csv',
                             posted_at=dt('2026-11-01T07:00:00+00:00'), batch_id='b2')
        outcome = apply_correction(self.KEY, state, deliveries,
                                   now=dt('2026-11-01T07:00:00+00:00'), eligible=True)
        # Nothing queued to supersede, no duplicate delivery, old snapshot intact.
        self.assertEqual(outcome['superseded'], [])
        self.assertIsNone(outcome['reissued_delivery_id'])
        self.assertEqual(outcome['active_delivery_id'], 'del-1')
        self.assertTrue(outcome['already_delivered'])
        self.assertEqual(deliveries[self.KEY][0].plan_snapshot['source_ref'], 'v1.csv')
        self.assertEqual(len(deliveries[self.KEY]), 1)

    def test_multiple_corrections_chain_versions(self):
        deliveries = {self.KEY: [self._queued('del-1')]}
        state1 = EditionState(self.KEY, EntryKind.receipt,
                              received_at=dt('2026-11-01T06:45:00+00:00'),
                              source_ref='v2.csv',
                              posted_at=dt('2026-11-01T05:30:00+00:00'), batch_id='b2')
        apply_correction(self.KEY, state1, deliveries,
                         now=dt('2026-11-01T05:30:00+00:00'), eligible=True)
        state2 = EditionState(self.KEY, EntryKind.receipt,
                              received_at=dt('2026-11-01T06:00:00+00:00'),
                              source_ref='v3.csv',
                              posted_at=dt('2026-11-01T05:45:00+00:00'), batch_id='b3')
        outcome = apply_correction(self.KEY, state2, deliveries,
                                   now=dt('2026-11-01T05:45:00+00:00'), eligible=True)
        self.assertEqual(len(deliveries[self.KEY]), 3)
        self.assertEqual(outcome['reissued_delivery_id'], f'del-{self.KEY}-v3')
        self.assertTrue(deliveries[self.KEY][1].superseded_at is not None)
        self.assertTrue(deliveries[self.KEY][2].is_pending)


if __name__ == '__main__':
    unittest.main()
