# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
from odoo import fields, Command
from odoo.tests import Form, HttpCase, new_test_user
from odoo.tools.float_utils import float_round

from odoo.addons.product.tests.common import ProductCommon

import json
import base64
from contextlib import contextmanager
from functools import wraps
from lxml import etree
from unittest import SkipTest
from unittest.mock import patch


class AccountTestInvoicingCommon(ProductCommon):
    # to override by the helper methods setup_country and setup_chart_template to adapt to a localization
    chart_template = False
    country_code = False

    @classmethod
    def safe_copy(cls, record):
        return record and record.copy()

    @staticmethod
    def setup_country(country_code):

        def _decorator(function):
            @wraps(function)
            def wrapper(self):
                self.country_code = country_code.upper()
                function(self)
            return wrapper

        return _decorator

    @staticmethod
    def setup_chart_template(chart_template):
        def _decorator(function):
            @wraps(function)
            def wrapper(self):
                self.chart_template = chart_template
                function(self)
            return wrapper

        return _decorator

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.company_data = cls.collect_company_accounting_data(cls.env.company)

        # ==== Taxes ====
        cls.tax_sale_a = cls.company_data['default_tax_sale']
        cls.tax_sale_b = cls.company_data['default_tax_sale'] and cls.company_data['default_tax_sale'].copy()
        cls.tax_purchase_a = cls.company_data['default_tax_purchase']
        cls.tax_purchase_b = cls.company_data['default_tax_purchase'] and cls.company_data['default_tax_purchase'].copy()
        cls.tax_armageddon = cls.setup_armageddon_tax('complex_tax', cls.company_data)

        # ==== Products ====
        cls.product_a = cls._create_product(
            name='product_a',
            lst_price=1000.0,
            standard_price=800.0
        )
        cls.product_b = cls._create_product(
            name='product_b',
            uom_id=cls.uom_dozen.id,
            uom_po_id=cls.uom_dozen.id,
            lst_price=200.0,
            standard_price=160.0,
            property_account_income_id=cls.copy_account(cls.company_data['default_account_revenue']).id,
            property_account_expense_id=cls.copy_account(cls.company_data['default_account_expense']).id,
            taxes_id=[Command.set((cls.tax_sale_a + cls.tax_sale_b).ids)],
            supplier_taxes_id=[Command.set((cls.tax_purchase_a + cls.tax_purchase_b).ids)],
        )

        # ==== Fiscal positions ====
        cls.fiscal_pos_a = cls.env['account.fiscal.position'].create({
            'name': 'fiscal_pos_a',
            'tax_ids': ([(0, None, {
                    'tax_src_id': cls.tax_sale_a.id,
                    'tax_dest_id': cls.tax_sale_b.id,
            })] if cls.tax_sale_b else []) + ([(0, None, {
                    'tax_src_id': cls.tax_purchase_a.id,
                    'tax_dest_id': cls.tax_purchase_b.id,
            })] if cls.tax_purchase_b else []),
            'account_ids': [
                (0, None, {
                    'account_src_id': cls.product_a.property_account_income_id.id,
                    'account_dest_id': cls.product_b.property_account_income_id.id,
                }),
                (0, None, {
                    'account_src_id': cls.product_a.property_account_expense_id.id,
                    'account_dest_id': cls.product_b.property_account_expense_id.id,
                }),
            ],
        })

        # ==== Payment terms ====
        cls.pay_terms_a = cls.env.ref('account.account_payment_term_immediate')
        cls.pay_terms_b = cls.env['account.payment.term'].create({
            'name': '30% Advance End of Following Month',
            'note': 'Payment terms: 30% Advance End of Following Month',
            'line_ids': [
                (0, 0, {
                    'value': 'percent',
                    'value_amount': 30.0,
                    'nb_days': 0,
                }),
                (0, 0, {
                    'value': 'percent',
                    'value_amount': 70.0,
                    'delay_type': 'days_after_end_of_next_month',
                    'nb_days': 0,
                }),
            ],
        })

        # ==== Partners ====
        cls.partner_a = cls.env['res.partner'].create({
            'name': 'partner_a',
            'property_payment_term_id': cls.pay_terms_a.id,
            'property_supplier_payment_term_id': cls.pay_terms_a.id,
            'property_account_receivable_id': cls.company_data['default_account_receivable'].id,
            'property_account_payable_id': cls.company_data['default_account_payable'].id,
            'company_id': False,
        })
        cls.partner_b = cls.env['res.partner'].create({
            'name': 'partner_b',
            'property_payment_term_id': cls.pay_terms_b.id,
            'property_supplier_payment_term_id': cls.pay_terms_b.id,
            'property_account_position_id': cls.fiscal_pos_a.id,
            'property_account_receivable_id': cls.company_data['default_account_receivable'].copy().id,
            'property_account_payable_id': cls.company_data['default_account_payable'].copy().id,
            'company_id': False,
        })

        # ==== Cash rounding ====
        cls.cash_rounding_a = cls.env['account.cash.rounding'].create({
            'name': 'add_invoice_line',
            'rounding': 0.05,
            'strategy': 'add_invoice_line',
            'profit_account_id': cls.company_data['default_account_revenue'].copy().id,
            'loss_account_id': cls.company_data['default_account_expense'].copy().id,
            'rounding_method': 'UP',
        })
        cls.cash_rounding_b = cls.env['account.cash.rounding'].create({
            'name': 'biggest_tax',
            'rounding': 0.05,
            'strategy': 'biggest_tax',
            'rounding_method': 'DOWN',
        })

        # ==== Payment methods ====
        bank_journal = cls.company_data['default_journal_bank']
        cls.inbound_payment_method_line = bank_journal.inbound_payment_method_line_ids[0]
        cls.outbound_payment_method_line = bank_journal.outbound_payment_method_line_ids[0]

    @classmethod
    def change_company_country(cls, company, country):
        company.country_id = country
        company.account_fiscal_country_id = country
        for model in ('account.tax', 'account.tax.group'):
            cls.env.add_to_compute(
                cls.env[model]._fields['country_id'],
                cls.env[model].search([('company_id', '=', company.id)]),
            )

    @classmethod
    def setup_other_company(cls, **kwargs):
        # OVERRIDE
        company = cls._create_company(**{'name': 'company_2'} | kwargs)
        return cls.collect_company_accounting_data(company)

    @classmethod
    def setup_independent_company(cls, **kwargs):
        return cls._create_company(name='company_1_data', **kwargs)

    @classmethod
    def setup_independent_user(cls):
        return new_test_user(
            cls.env,
            name='Because I am accountman!',
            login='accountman',
            password='accountman',
            email='accountman@test.com',
            groups_id=cls.get_default_groups().ids,
            company_id=cls.env.company.id,
        )

    @classmethod
    def _create_company(cls, **create_values):
        if cls.country_code:
            country = cls.env['res.country'].search([('code', '=', cls.country_code.upper())])
            if not country:
                raise ValueError('Invalid country code')
            if 'country_id' not in create_values:
                create_values['country_id'] = country.id
            if 'currency_id' not in create_values:
                create_values['currency_id'] = country.currency_id.id
        company = super()._create_company(**create_values)
        cls._use_chart_template(company, cls.chart_template)
        # if the currency_id was defined explicitly (or via the country), it should override the one from the coa
        if create_values.get('currency_id'):
            company.currency_id = create_values['currency_id']
        return company

    @classmethod
    def _create_product(cls, **create_values):
        # OVERRIDE
        create_values.setdefault('property_account_income_id', cls.company_data['default_account_revenue'].id)
        create_values.setdefault('property_account_expense_id', cls.company_data['default_account_expense'].id)
        create_values.setdefault('taxes_id', [Command.set(cls.tax_sale_a.ids)])
        return super()._create_product(**create_values)

    @classmethod
    def get_default_groups(cls):
        groups = super().get_default_groups()
        return groups | cls.env.ref('account.group_account_manager') | cls.env.ref('account.group_account_user')

    @classmethod
    def setup_other_currency(cls, code, **kwargs):
        if 'rates' not in kwargs:
            return super().setup_other_currency(code, rates=[('2016-01-01', 3.0), ('2017-01-01', 2.0)], **kwargs)
        return super().setup_other_currency(code, **kwargs)

    @classmethod
    def _use_chart_template(cls, company, chart_template_ref=None):
        chart_template_ref = chart_template_ref or cls.env['account.chart.template']._guess_chart_template(company.country_id)
        template_vals = cls.env['account.chart.template']._get_chart_template_mapping()[chart_template_ref]
        template_module = cls.env['ir.module.module']._get(template_vals['module'])
        if template_module.state != 'installed':
            raise SkipTest(f"Module required for the test is not installed ({template_module.name})")

        # Install the chart template
        cls.env['account.chart.template'].try_loading(chart_template_ref, company=company, install_demo=False)
        if not company.account_fiscal_country_id:
            company.account_fiscal_country_id = cls.env.ref('base.us')

    @classmethod
    def collect_company_accounting_data(cls, company):
        return {
            'company': company,
            'currency': company.currency_id,
            'default_account_revenue': cls.env['account.account'].search([
                    ('company_id', '=', company.id),
                    ('account_type', '=', 'income'),
                    ('id', '!=', company.account_journal_early_pay_discount_gain_account_id.id)
                ], limit=1),
            'default_account_expense': cls.env['account.account'].search([
                    ('company_id', '=', company.id),
                    ('account_type', '=', 'expense'),
                    ('id', '!=', company.account_journal_early_pay_discount_loss_account_id.id)
                ], limit=1),
            'default_account_receivable': cls.env['ir.property'].with_company(company)._get(
                'property_account_receivable_id', 'res.partner'
            ),
            'default_account_payable': cls.env['account.account'].search([
                    ('company_id', '=', company.id),
                    ('account_type', '=', 'liability_payable')
                ], limit=1),
            'default_account_assets': cls.env['account.account'].search([
                    ('company_id', '=', company.id),
                    ('account_type', '=', 'asset_fixed')
                ], limit=1),
            'default_account_deferred_expense': cls.env['account.account'].search([
                    ('company_id', '=', company.id),
                    ('account_type', '=', 'asset_current')
                ], limit=1),
            'default_account_deferred_revenue': cls.env['account.account'].search([
                    ('company_id', '=', company.id),
                    ('account_type', '=', 'liability_current')
                ], limit=1),
            'default_account_tax_sale': company.account_sale_tax_id.mapped('invoice_repartition_line_ids.account_id'),
            'default_account_tax_purchase': company.account_purchase_tax_id.mapped('invoice_repartition_line_ids.account_id'),
            'default_journal_misc': cls.env['account.journal'].search([
                    ('company_id', '=', company.id),
                    ('type', '=', 'general')
                ], limit=1),
            'default_journal_sale': cls.env['account.journal'].search([
                    ('company_id', '=', company.id),
                    ('type', '=', 'sale')
                ], limit=1),
            'default_journal_purchase': cls.env['account.journal'].search([
                    ('company_id', '=', company.id),
                    ('type', '=', 'purchase')
                ], limit=1),
            'default_journal_bank': cls.env['account.journal'].search([
                    ('company_id', '=', company.id),
                    ('type', '=', 'bank')
                ], limit=1),
            'default_journal_cash': cls.env['account.journal'].search([
                    ('company_id', '=', company.id),
                    ('type', '=', 'cash')
                ], limit=1),
            'default_tax_sale': company.account_sale_tax_id,
            'default_tax_purchase': company.account_purchase_tax_id,
        }

    @classmethod
    def copy_account(cls, account, default=None):
        suffix_nb = 1
        while True:
            new_code = '%s.%s' % (account.code, suffix_nb)
            if account.search_count([('company_id', '=', account.company_id.id), ('code', '=', new_code)]):
                suffix_nb += 1
            else:
                return account.copy(default={'code': new_code, 'name': account.name, **(default or {})})

    @classmethod
    def setup_armageddon_tax(cls, tax_name, company_data, **kwargs):
        type_tax_use = kwargs.get('type_tax_use', 'sale')
        cash_basis_transition_account = company_data['default_account_tax_sale'] and company_data['default_account_tax_sale'].copy()
        return cls.env['account.tax'].create({
            'name': '%s (group)' % tax_name,
            'amount_type': 'group',
            'amount': 0.0,
            'country_id': company_data['company'].account_fiscal_country_id.id,
            'children_tax_ids': [
                (0, 0, {
                    'name': '%s (child 1)' % tax_name,
                    'amount_type': 'percent',
                    'amount': 20.0,
                    'type_tax_use': type_tax_use,
                    'country_id': company_data['company'].account_fiscal_country_id.id,
                    'price_include': True,
                    'include_base_amount': True,
                    'tax_exigibility': 'on_invoice',
                    'invoice_repartition_line_ids': [
                        (0, 0, {
                            'repartition_type': 'base',
                        }),
                        (0, 0, {
                            'factor_percent': 40,
                            'repartition_type': 'tax',
                            'account_id': company_data['default_account_tax_sale'].id,
                        }),
                        (0, 0, {
                            'factor_percent': 60,
                            'repartition_type': 'tax',
                            # /!\ No account set.
                        }),
                    ],
                    'refund_repartition_line_ids': [
                        (0, 0, {
                            'repartition_type': 'base',
                        }),
                        (0, 0, {
                            'factor_percent': 40,
                            'repartition_type': 'tax',
                            'account_id': company_data['default_account_tax_sale'].id,
                        }),
                        (0, 0, {
                            'factor_percent': 60,
                            'repartition_type': 'tax',
                            # /!\ No account set.
                        }),
                    ],
                }),
                (0, 0, {
                    'name': '%s (child 2)' % tax_name,
                    'amount_type': 'percent',
                    'amount': 10.0,
                    'type_tax_use': type_tax_use,
                    'country_id': company_data['company'].account_fiscal_country_id.id,
                    'tax_exigibility': 'on_payment',
                    'cash_basis_transition_account_id': cash_basis_transition_account.id,
                    'invoice_repartition_line_ids': [
                        (0, 0, {
                            'repartition_type': 'base',
                        }),
                        (0, 0, {
                            'repartition_type': 'tax',
                            'account_id': company_data['default_account_tax_sale'].id,
                        }),
                    ],
                    'refund_repartition_line_ids': [
                        (0, 0, {
                            'repartition_type': 'base',
                        }),

                        (0, 0, {
                            'repartition_type': 'tax',
                            'account_id': company_data['default_account_tax_sale'].id,
                        }),
                    ],
                }),
            ],
            **kwargs,
        })

    @classmethod
    def init_invoice(cls, move_type, partner=None, invoice_date=None, post=False, products=None, amounts=None, taxes=None, company=False, currency=None, journal=None):
        products = [] if products is None else products
        amounts = [] if amounts is None else amounts
        move_form = Form(cls.env['account.move'] \
                    .with_company(company or cls.env.company) \
                    .with_context(default_move_type=move_type))
        move_form.invoice_date = invoice_date or fields.Date.from_string('2019-01-01')
        # According to the state or type of the invoice, the date field is sometimes visible or not
        # Besides, the date field can be put multiple times in the view
        # "invisible": "['|', ('state', '!=', 'draft'), ('auto_post', '!=', 'at_date')]"
        # "invisible": ['|', '|', ('state', '!=', 'draft'), ('auto_post', '=', 'no'), ('auto_post', '=', 'at_date')]
        # "invisible": "['&', ('move_type', 'in', ['out_invoice', 'out_refund', 'out_receipt']), ('quick_edit_mode', '=', False)]"
        # :TestAccountMoveOutInvoiceOnchanges, :TestAccountMoveOutRefundOnchanges, .test_00_debit_note_out_invoice, :TestAccountEdi
        if not move_form._get_modifier('date', 'invisible'):
            move_form.date = move_form.invoice_date
        move_form.partner_id = partner or cls.partner_a
        if journal:
            move_form.journal_id = journal
        if currency:
            move_form.currency_id = currency

        for product in (products or []):
            with move_form.invoice_line_ids.new() as line_form:
                line_form.product_id = product
                if taxes is not None:
                    line_form.tax_ids.clear()
                    for tax in taxes:
                        line_form.tax_ids.add(tax)

        for amount in (amounts or []):
            with move_form.invoice_line_ids.new() as line_form:
                line_form.name = "test line"
                line_form.price_unit = amount
                if taxes is not None:
                    line_form.tax_ids.clear()
                    for tax in taxes:
                        line_form.tax_ids.add(tax)

        rslt = move_form.save()

        if post:
            rslt.action_post()

        return rslt

    @classmethod
    def init_payment(cls, amount, post=False, date=None, partner=None, currency=None):
        payment = cls.env['account.payment'].create({
            'amount': abs(amount),
            'date': date or fields.Date.from_string('2019-01-01'),
            'payment_type': 'inbound' if amount >= 0 else 'outbound',
            'partner_type': 'customer' if amount >= 0 else 'supplier',
            'partner_id': (partner or cls.partner_a).id,
            'currency_id': (currency or cls.company_data['currency']).id,
        })
        if post:
            payment.action_post()
        return payment

    def create_line_for_reconciliation(self, balance, amount_currency, currency, move_date, account_1=None, partner=None):
        write_off_account_to_be_reconciled = account_1 if account_1 else self.receivable_account
        move = self.env['account.move'].create({
            'move_type': 'entry',
            'date': move_date,
            'line_ids': [
                Command.create({
                    'debit': balance if balance > 0.0 else 0.0,
                    'credit': -balance if balance < 0.0 else 0.0,
                    'amount_currency': amount_currency,
                    'account_id': write_off_account_to_be_reconciled.id,
                    'currency_id': currency.id,
                    'partner_id': partner.id if partner else None,
                }),
                Command.create({
                    'debit': -balance if balance < 0.0 else 0.0,
                    'credit': balance if balance > 0.0 else 0.0,
                    'amount_currency': -amount_currency,
                    'account_id': self.company_data['default_account_revenue'].id,
                    'currency_id': currency.id,
                    'partner_id': partner.id if partner else None,
                }),
            ],
        })
        move.action_post()
        line = move.line_ids.filtered(lambda x: x.account_id == write_off_account_to_be_reconciled)

        self.assertRecordValues(line, [{
            'amount_residual': balance,
            'amount_residual_currency': amount_currency,
            'reconciled': False,
        }])

        return line

    def assertInvoiceValues(self, move, expected_lines_values, expected_move_values):
        def sort_lines(lines):
            return lines.sorted(lambda line: (line.sequence, not bool(line.tax_line_id), line.name or line.product_id.display_name or '', line.balance))
        self.assertRecordValues(sort_lines(move.line_ids.sorted()), expected_lines_values)
        self.assertRecordValues(move, [expected_move_values])

    def assert_tax_totals(self, tax_totals, currency, expected_values):
        main_keys_to_ignore = {'formatted_amount_total', 'formatted_amount_untaxed'}
        group_keys_to_ignore = {'group_key', 'formatted_tax_group_amount', 'formatted_tax_group_base_amount', 'display_formatted_tax_group_base_amount'}
        subtotals_keys_to_ignore = {'formatted_amount'}
        comp_curr_keys = {'tax_group_amount_company_currency', 'tax_group_base_amount_company_currency', 'amount_company_currency'}
        to_compare = dict(tax_totals)

        for key in main_keys_to_ignore:
            del to_compare[key]

        # Exclude company currency fields if not checked.
        need_comp_curr_fields = False
        for subtotals in expected_values.get('subtotals', []):
            if any(x in subtotals for x in comp_curr_keys):
                need_comp_curr_fields = True
                break
        if not need_comp_curr_fields:
            for groups in expected_values.get('groups_by_subtotal').values():
                if any(x in groups for x in comp_curr_keys):
                    need_comp_curr_fields = True
                    break
        if not need_comp_curr_fields:
            for key in comp_curr_keys:
                group_keys_to_ignore.add(key)
                subtotals_keys_to_ignore.add(key)

        for group_key, groups in to_compare['groups_by_subtotal'].items():
            expected_groups = expected_values['groups_by_subtotal'].get(group_key)
            for i, group in enumerate(groups):
                for key in group_keys_to_ignore:
                    group.pop(key, None)

                # Fix monetary field to avoid 40.8 != 40.8000000004
                expected_group = i < len(expected_groups) and expected_groups[i]
                if expected_group:
                    for monetary_field in ('tax_group_amount', 'tax_group_base_amount'):
                        if (
                            expected_group
                            and monetary_field in expected_group
                            and currency.compare_amounts(
                                expected_group[monetary_field],
                                group[monetary_field],
                            ) == 0
                        ):
                            expected_group[monetary_field] = group[monetary_field]

        for key in subtotals_keys_to_ignore:
            for subtotal in to_compare['subtotals']:
                subtotal.pop(key, None)

        self.assertEqual(to_compare, expected_values)

    def assert_document_tax_totals(self, document, expected_values):
        document.invalidate_model(fnames=['tax_totals'])
        self.assert_tax_totals(document.tax_totals, document.currency_id, expected_values)

    def assert_invoice_outstanding_to_reconcile_widget(self, invoice, expected_amounts):
        """ Check the outstanding widget before the reconciliation.
        :param invoice:             An invoice.
        :param expected_amounts:    A map <move_id> -> <amount>
        """
        invoice.invalidate_recordset(['invoice_outstanding_credits_debits_widget'])
        widget_vals = invoice.invoice_outstanding_credits_debits_widget

        if widget_vals:
            current_amounts = {vals['move_id']: vals['amount'] for vals in widget_vals['content']}
        else:
            current_amounts = {}
        self.assertDictEqual(current_amounts, expected_amounts)

    def assert_invoice_outstanding_reconciled_widget(self, invoice, expected_amounts):
        """ Check the outstanding widget after the reconciliation.
        :param invoice:             An invoice.
        :param expected_amounts:    A map <move_id> -> <amount>
        """
        invoice.invalidate_recordset(['invoice_payments_widget'])
        widget_vals = invoice.invoice_payments_widget

        if widget_vals:
            current_amounts = {vals['move_id']: vals['amount'] for vals in widget_vals['content']}
        else:
            current_amounts = {}
        self.assertDictEqual(current_amounts, expected_amounts)

    ####################################################
    # Xml Comparison
    ####################################################

    def _turn_node_as_dict_hierarchy(self, node, path=''):
        ''' Turn the node as a python dictionary to be compared later with another one.
        Allow to ignore the management of namespaces.
        :param node:    A node inside an xml tree.
        :param path:    The optional path of tags for recursive call.
        :return:        A python dictionary.
        '''
        tag_split = node.tag.split('}')
        tag_wo_ns = tag_split[-1]
        attrib_wo_ns = {k: v for k, v in node.attrib.items() if '}' not in k}
        full_path = f'{path}/{tag_wo_ns}'
        return {
            'tag': tag_wo_ns,
            'full_path': full_path,
            'namespace': None if len(tag_split) < 2 else tag_split[0],
            'text': (node.text or '').strip(),
            'attrib': attrib_wo_ns,
            'children': [
                self._turn_node_as_dict_hierarchy(child_node, path=path)
                for child_node in node.getchildren()
            ],
        }

    def assertXmlTreeEqual(self, xml_tree, expected_xml_tree):
        ''' Compare two lxml.etree.
        :param xml_tree:            The current tree.
        :param expected_xml_tree:   The expected tree.
        '''

        def assertNodeDictEqual(node_dict, expected_node_dict):
            ''' Compare nodes created by the `_turn_node_as_dict_hierarchy` method.
            :param node_dict:           The node to compare with.
            :param expected_node_dict:  The expected node.
            '''
            if expected_node_dict['text'] == '___ignore___':
                return
            # Check tag.
            self.assertEqual(node_dict['tag'], expected_node_dict['tag'])

            # Check attributes.
            for k, v in expected_node_dict['attrib'].items():
                if v == '___ignore___':
                    node_dict['attrib'][k] = '___ignore___'

            self.assertDictEqual(
                node_dict['attrib'],
                expected_node_dict['attrib'],
                f"Element attributes are different for node {node_dict['full_path']}",
            )

            # Check text.
            if expected_node_dict['text'] != '___ignore___':
                self.assertEqual(
                    node_dict['text'],
                    expected_node_dict['text'],
                    f"Element text are different for node {node_dict['full_path']}",
                )

            # Check children.
            self.assertEqual(
                [child['tag'] for child in node_dict['children']],
                [child['tag'] for child in expected_node_dict['children']],
                f"Number of children elements for node {node_dict['full_path']} is different.",
            )

            for child_node_dict, expected_child_node_dict in zip(node_dict['children'], expected_node_dict['children']):
                assertNodeDictEqual(child_node_dict, expected_child_node_dict)

        assertNodeDictEqual(
            self._turn_node_as_dict_hierarchy(xml_tree),
            self._turn_node_as_dict_hierarchy(expected_xml_tree),
        )

    def with_applied_xpath(self, xml_tree, xpath):
        ''' Applies the xpath to the xml_tree passed as parameter.
        :param xml_tree:    An instance of etree.
        :param xpath:       The xpath to apply as a string.
        :return:            The resulting etree after applying the xpaths.
        '''
        diff_xml_tree = etree.fromstring('<data>%s</data>' % xpath)
        return self.env['ir.ui.view'].apply_inheritance_specs(xml_tree, diff_xml_tree)

    def get_xml_tree_from_attachment(self, attachment):
        ''' Extract an instance of etree from an ir.attachment.
        :param attachment:  An ir.attachment.
        :return:            An instance of etree.
        '''
        return etree.fromstring(base64.b64decode(attachment.with_context(bin_size=False).datas))

    def get_xml_tree_from_string(self, xml_tree_str):
        ''' Convert the string passed as parameter to an instance of etree.
        :param xml_tree_str:    A string representing an xml.
        :return:                An instance of etree.
        '''
        return etree.fromstring(xml_tree_str)

    @contextmanager
    def enter_test_mode(self):
        """
        Make so that all new cursors opened on this database registry
        reuse the one currently used by the test.

        Useful for printing PDFs inside a TransactionCase test when
        using a HttpCase is not possible/desirable.
        """
        self.env.registry.enter_test_mode(self.env.cr)
        try:
            yield
        finally:
            self.env.registry.leave_test_mode()


class AccountTestMockOnlineSyncCommon(HttpCase):
    def start_tour(self, url_path, tour_name, step_delay=None, **kwargs):
        with self.mock_online_sync_favorite_institutions():
            super().start_tour(url_path, tour_name, step_delay, **kwargs)

    @classmethod
    @contextmanager
    def mock_online_sync_favorite_institutions(cls):
        def get_institutions(*args, **kwargs):
            return [
                {
                    'country': 'US',
                    'id': 3245,
                    'name': 'BMO Business Banking',
                    'picture': '/base/static/img/logo_white.png',
                },
                {
                    'country': 'US',
                    'id': 8192,
                    'name': 'Banc of California',
                    'picture': '/base/static/img/logo_white.png'
                },
            ]
        with patch.object(
             target=cls.registry['account.journal'],
             attribute='fetch_online_sync_favorite_institutions',
             new=get_institutions,
             create=True):
            yield


class AccountTestInvoicingHttpCommon(AccountTestInvoicingCommon, AccountTestMockOnlineSyncCommon):
    pass


class TestTaxCommon(AccountTestInvoicingHttpCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.number = 0
        cls.maxDiff = None

    def new_currency(self, rounding):
        self.number += 1
        return self.env.company.currency_id.copy({
            'name': f"{self.number}",
            'rounding': rounding,
        })

    def group_of_taxes(self, taxes, **kwargs):
        self.number += 1
        return self.env['account.tax'].create({
            **kwargs,
            'name': f"group_({self.number})",
            'amount_type': 'group',
            'children_tax_ids': [Command.set(taxes.ids)],
        })

    def percent_tax(self, amount, **kwargs):
        self.number += 1
        return self.env['account.tax'].create({
            **kwargs,
            'name': f"percent_{amount}_({self.number})",
            'amount_type': 'percent',
            'amount': amount,
        })

    def division_tax(self, amount, **kwargs):
        self.number += 1
        return self.env['account.tax'].create({
            **kwargs,
            'name': f"division_{amount}_({self.number})",
            'amount_type': 'division',
            'amount': amount,
        })

    def fixed_tax(self, amount, **kwargs):
        self.number += 1
        return self.env['account.tax'].create({
            **kwargs,
            'name': f"fixed_{amount}_({self.number})",
            'amount_type': 'fixed',
            'amount': amount,
        })

    def _prepare_taxes_computation_test(self, taxes, price_unit, expected_values, evaluation_context_kwargs=None, compute_kwargs=None):
        """ Prepare the values to test a taxes computation.

        :param taxes:                       A recordset of account.tax.
        :param price_unit:                  The price unit on which apply the computation of taxes.
        :param expected_values:             The expected result of '_eval_taxes_computation'.
        :param evaluation_context_kwargs:   Some extra values about the computation:
            * quantity:                 1 by default.
            * product:                  An optional product record if you are using taxes that depend on the product.
            * rounding_method:          The taxes rounding method that is 'round_per_line' by default.
            * excluded_special_modes:   By default, when using the 'round_globally' method, both special modes are tested as well
                                        ('total_excluded' & 'total_included') to ensure the computations are symmetrical.
                                        The taxes computation is called first with 'special_mode=False'.
                                        Then, we call it again with "special_mode='total_excluded'" using the 'total_excluded' found during
                                        the previous evaluation as price_unit.
                                        Then, we call it again but this time using 'total_included'.
                                        The all 3 computations should give exactly the same expected results. In rare cases, you would want
                                        to use a configuration that is not symmetrical. In that case, 'excluded_special_modes' could be
                                        used to excluded 'total_excluded', 'total_included' or both.
        :param compute_kwargs:  Extra parameters to be passed to the '_prepare_taxes_computation' method.
        :return: A dictionary representing a test that will be used py-side & js-side to assert the taxes computation.
        """
        evaluation_context_kwargs = evaluation_context_kwargs or {}
        quantity = evaluation_context_kwargs.pop('quantity', 1)
        product = evaluation_context_kwargs.pop('product', None)
        compute_kwargs = compute_kwargs or {}
        is_round_globally = evaluation_context_kwargs.get('rounding_method') == 'round_globally'
        excluded_special_modes = evaluation_context_kwargs.pop('excluded_special_modes', [])

        taxes_data = taxes._convert_to_dict_for_taxes_computation()
        product_values = taxes._eval_taxes_computation_turn_to_product_values(taxes_data, product=product)
        return {
            'expected_values': expected_values,
            'params': {
                'test': 'taxes_computation',
                'taxes_data': taxes_data,
                'price_unit': price_unit,
                'quantity': quantity,
                'product_values': product_values,
                'evaluation_context_kwargs': {
                    'round_price_include': False,
                    **evaluation_context_kwargs,
                },
                'compute_kwargs': compute_kwargs,
                'is_round_globally': is_round_globally,
                'excluded_special_modes': excluded_special_modes,
            },
        }

    def _prepare_adapt_price_unit_to_another_taxes_test(self, price_unit, original_taxes, new_taxes, expected_price_unit, product=None):
        original_taxes_data = original_taxes._convert_to_dict_for_taxes_computation()
        new_taxes_data = new_taxes._convert_to_dict_for_taxes_computation()
        product_values = new_taxes._eval_taxes_computation_turn_to_product_values(
            original_taxes_data + new_taxes_data,
            product=product,
        )
        return {
            'expected_price_unit': expected_price_unit,
            'params': {
                'test': 'adapt_price_unit_to_another_taxes',
                'original_taxes_data': original_taxes_data,
                'new_taxes_data': new_taxes_data,
                'price_unit': price_unit,
                'product_values': product_values,
            },
        }

    def _add_test_py_results(self, test):
        AccountTax = self.env['account.tax']
        params = test['params']
        if params['test'] == 'taxes_computation':
            evaluation_context = AccountTax._eval_taxes_computation_prepare_context(
                params['price_unit'],
                params['quantity'],
                params['product_values'],
                **params['evaluation_context_kwargs'],
            )
            taxes_computation = AccountTax._prepare_taxes_computation(params['taxes_data'], **params['compute_kwargs'])
            test['py_results'] = {
                'results': AccountTax._eval_taxes_computation(taxes_computation, evaluation_context),
            }

            if params['is_round_globally']:
                taxes_computation = AccountTax._prepare_taxes_computation(
                    params['taxes_data'],
                    **params['compute_kwargs'],
                    special_mode='total_excluded',
                )
                evaluation_context = AccountTax._eval_taxes_computation_prepare_context(
                    test['py_results']['results']['total_excluded'] / params['quantity'],
                    params['quantity'],
                    params['product_values'],
                    **params['evaluation_context_kwargs'],
                )
                test['py_results']['total_excluded_results'] = AccountTax._eval_taxes_computation(taxes_computation, evaluation_context)
                taxes_computation = AccountTax._prepare_taxes_computation(
                    params['taxes_data'],
                    **params['compute_kwargs'],
                    special_mode='total_included',
                )
                evaluation_context = AccountTax._eval_taxes_computation_prepare_context(
                    test['py_results']['results']['total_included'] / params['quantity'],
                    params['quantity'],
                    params['product_values'],
                    **params['evaluation_context_kwargs'],
                )
                test['py_results']['total_included_results'] = AccountTax._eval_taxes_computation(taxes_computation, evaluation_context)
        elif params['test'] == 'adapt_price_unit_to_another_taxes':
            test['py_results'] = self.env['account.tax']._adapt_price_unit_to_another_taxes(
                params['price_unit'],
                params['product_values'],
                params['original_taxes_data'],
                params['new_taxes_data'],
            )
        else:
            assert False, f"Unknown tax test method: {params['test']}"

    def _add_tests_py_results(self, tests):
        for test in tests:
            self._add_test_py_results(test)

    def _add_tests_js_results(self, tests):
        self.env['ir.config_parameter'].set_param(
            'account.tests_shared_js_python',
            json.dumps([test['params'] for test in tests]),
        )

        self.start_tour('/account/init_tests_shared_js_python', 'tests_shared_js_python', login=self.env.user.login)
        results = json.loads(self.env['ir.config_parameter'].get_param('account.tests_shared_js_python', '[]'))
        self.assertEqual(len(results), len(tests))

        for test, result in zip(tests, results):
            test['js_results'] = result

    def _assert_sub_test_taxes_computation(self, test, results):

        def compare_taxes_computation_values(results, expected_values, rounding):
            self.assertEqual(
                float_round(results['total_included'], precision_rounding=rounding),
                float_round(expected_values['total_included'], precision_rounding=rounding),
            )
            self.assertEqual(
                float_round(results['total_excluded'], precision_rounding=rounding),
                float_round(expected_values['total_excluded'], precision_rounding=rounding),
            )
            self.assertEqual(len(results['taxes_data']), len(expected_values['taxes_data']))
            for tax_data, (expected_base, expected_tax) in zip(results['taxes_data'], expected_values['taxes_data']):
                self.assertEqual(
                    float_round(tax_data['base'], precision_rounding=rounding),
                    float_round(expected_base, precision_rounding=rounding),
                )
                self.assertEqual(
                    float_round(tax_data['tax_amount_factorized'], precision_rounding=rounding),
                    float_round(expected_tax, precision_rounding=rounding),
                )

        params = test['params']
        is_round_globally = params['is_round_globally']
        excluded_special_modes = params['excluded_special_modes']
        rounding = 0.000001 if is_round_globally else 0.01
        compare_taxes_computation_values(results['results'], test['expected_values'], rounding)

        # Check the special modes in case of round_globally.
        if is_round_globally:
            # special_mode == 'total_excluded'.
            if 'total_excluded' not in excluded_special_modes:
                compare_taxes_computation_values(results['total_excluded_results'], test['expected_values'], rounding)
                delta = sum(x['tax_amount_factorized'] for x in results['total_excluded_results']['taxes_data'] if x['_original_price_include'])

                self.assertEqual(
                    float_round(
                        (results['total_excluded_results']['total_excluded'] + delta) / params['quantity'],
                        precision_rounding=rounding,
                    ),
                    float_round(params['price_unit'], precision_rounding=rounding),
                )

            # special_mode == 'total_included'.
            if 'total_included' not in excluded_special_modes:
                compare_taxes_computation_values(results['total_included_results'], test['expected_values'], rounding)
                delta = sum(x['tax_amount_factorized'] for x in results['total_included_results']['taxes_data'] if not x['_original_price_include'])

                self.assertEqual(
                    float_round(
                        (results['total_included_results']['total_included'] - delta) / params['quantity'],
                        precision_rounding=rounding,
                    ),
                    float_round(params['price_unit'], precision_rounding=rounding),
                )

    def _assert_sub_test(self, test, results):
        params = test['params']
        if params['test'] == 'taxes_computation':
            self._assert_sub_test_taxes_computation(test, results)
        elif params['test'] == 'adapt_price_unit_to_another_taxes':
            self.assertEqual(results, test['expected_price_unit'])

    def _assert_test(self, test, results_keys):
        for results_key in results_keys:
            with self.subTest(test=test['params']['test'], results_key=results_key):
                results = test[results_key]
                self._assert_sub_test(test, results)

    def _assert_tests(self, tests, mode='py_js'):
        results_keys = []
        if mode in ('py_js', 'py'):
            self._add_tests_py_results(tests)
            results_keys.append('py_results')
        if mode in ('py_js', 'js'):
            self._add_tests_js_results(tests)
            results_keys.append('js_results')
        for i, test in enumerate(tests, start=1):
            with self.subTest(test_index=i):
                self._assert_test(test, results_keys)
