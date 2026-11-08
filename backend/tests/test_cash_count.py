from decimal import Decimal

from django.test import SimpleTestCase

from sales.cash_count import CURRENCY_DEFS, calculate_removal, calculate_totals


class CalculateTotalsTests(SimpleTestCase):
    def test_breakdown_has_an_entry_for_every_denomination(self):
        totals = calculate_totals({})

        self.assertEqual(
            [item['key'] for item in totals['breakdown']],
            [key for _, key, _ in CURRENCY_DEFS],
        )

    def test_one_of_each_denomination_splits_coins_and_bills(self):
        counts = {key: 1 for _, key, _ in CURRENCY_DEFS}

        totals = calculate_totals(counts)

        self.assertEqual(totals['coins'], Decimal('3.91'))
        self.assertEqual(totals['bills'], Decimal('188.00'))
        self.assertEqual(totals['grand_total'], Decimal('191.91'))

    def test_breakdown_amount_is_count_times_denomination(self):
        totals = calculate_totals({'twenties': 3, 'quarters': 4})
        breakdown = {item['key']: item for item in totals['breakdown']}

        self.assertEqual(breakdown['twenties']['amount'], Decimal('60.00'))
        self.assertEqual(breakdown['twenties']['count'], 3)
        self.assertEqual(breakdown['quarters']['amount'], Decimal('1.00'))
        self.assertEqual(breakdown['quarters']['category'], 'coins')

    def test_same_value_coin_and_bill_are_counted_separately(self):
        totals = calculate_totals({'dollars': 2, 'ones': 3, 'two_dollars': 1, 'twos': 4})
        breakdown = {item['key']: item for item in totals['breakdown']}

        self.assertEqual(breakdown['dollars']['category'], 'coins')
        self.assertEqual(breakdown['ones']['category'], 'bills')
        self.assertEqual(breakdown['two_dollars']['category'], 'coins')
        self.assertEqual(breakdown['twos']['category'], 'bills')
        self.assertEqual(totals['coins'], Decimal('4.00'))
        self.assertEqual(totals['bills'], Decimal('11.00'))

    def test_missing_keys_default_to_zero(self):
        totals = calculate_totals({'pennies': 7})
        breakdown = {item['key']: item for item in totals['breakdown']}

        self.assertEqual(breakdown['pennies']['amount'], Decimal('0.07'))
        self.assertEqual(breakdown['hundreds']['amount'], Decimal('0'))

    def test_unknown_keys_are_ignored(self):
        totals = calculate_totals({'hundreds': 1, 'doubloons': 10})

        self.assertEqual(totals['grand_total'], Decimal('100.00'))

    def test_counts_input_is_not_mutated(self):
        counts = {'ones': 2}

        calculate_totals(counts)

        self.assertEqual(counts, {'ones': 2})


class CalculateRemovalTests(SimpleTestCase):
    def test_zero_amount_removes_nothing(self):
        result = calculate_removal(Decimal('0'), {'twenties': 3})

        self.assertEqual(result['total_removed'], Decimal('0'))
        self.assertEqual(result['remaining'], Decimal('0'))
        self.assertEqual(result['counts']['twenties'], 0)

    def test_largest_denominations_are_taken_first(self):
        result = calculate_removal(
            Decimal('85.00'),
            {'hundreds': 1, 'fifties': 1, 'twenties': 2, 'fives': 1},
        )

        self.assertEqual(result['counts']['fifties'], 1)
        self.assertEqual(result['counts']['twenties'], 1)
        self.assertEqual(result['counts']['fives'], 1)
        self.assertEqual(result['counts']['hundreds'], 0)
        self.assertEqual(result['total_removed'], Decimal('75.00'))
        self.assertEqual(result['remaining'], Decimal('10.00'))

    def test_removal_stops_when_requested_amount_is_covered(self):
        result = calculate_removal(Decimal('45.00'), {'twenties': 5, 'tens': 5})

        self.assertEqual(result['counts']['twenties'], 2)
        self.assertEqual(result['counts']['tens'], 0)
        self.assertEqual(result['total_removed'], Decimal('40.00'))
        self.assertEqual(result['remaining'], Decimal('5.00'))

    def test_exact_removal_with_all_denominations_leaves_nothing(self):
        available = {key: 1 for _, key, _ in CURRENCY_DEFS}

        result = calculate_removal(Decimal('191.91'), available)

        self.assertEqual(result['total_removed'], Decimal('191.91'))
        self.assertEqual(result['remaining'], Decimal('0'))

    def test_removal_falls_back_to_coins(self):
        result = calculate_removal(
            Decimal('0.41'),
            {'quarters': 1, 'dimes': 1, 'nickels': 1, 'pennies': 1},
        )

        self.assertEqual(result['counts']['quarters'], 1)
        self.assertEqual(result['counts']['dimes'], 1)
        self.assertEqual(result['counts']['nickels'], 1)
        self.assertEqual(result['counts']['pennies'], 1)
        self.assertEqual(result['category_totals']['coins'], Decimal('0.41'))
        self.assertEqual(result['remaining'], Decimal('0'))

    def test_removal_reports_shortfall_when_available_is_insufficient(self):
        result = calculate_removal(Decimal('100.00'), {'twenties': 2})

        self.assertEqual(result['total_removed'], Decimal('40.00'))
        self.assertEqual(result['remaining'], Decimal('60.00'))

    def test_amounts_match_counts_times_denomination(self):
        result = calculate_removal(
            Decimal('57.00'),
            {'fifties': 1, 'fives': 2, 'ones': 20},
        )

        self.assertEqual(result['amounts']['fifties'], Decimal('50.00'))
        self.assertEqual(result['amounts']['fives'], Decimal('5.00'))
        self.assertEqual(result['amounts']['ones'], Decimal('2.00'))
        self.assertEqual(result['category_totals']['bills'], Decimal('57.00'))

    def test_removal_does_not_mutate_available_counts(self):
        available = {'twenties': 2, 'tens': 1}

        calculate_removal(Decimal('30.00'), available)

        self.assertEqual(available, {'twenties': 2, 'tens': 1})
