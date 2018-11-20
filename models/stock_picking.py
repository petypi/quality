# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    check_ids = fields.One2many('quality.check', 'picking_id', 'Checks')
    quality_check_todo = fields.Boolean('Pending checks', compute='_compute_check')
    quality_check_fail = fields.Boolean(compute='_compute_check')
    quality_alert_ids = fields.One2many('quality.alert', 'picking_id', 'Alerts')
    quality_alert_count = fields.Integer(compute='_compute_quality_alert_count')

    @api.multi
    def _compute_check(self):
        for picking in self:
            todo = False
            fail = False
            for check in picking.check_ids:
                if check.quality_state == 'none':
                    todo = True
                elif check.quality_state == 'fail':
                    fail = True
                if fail and todo:
                    break
            picking.quality_check_fail = fail
            picking.quality_check_todo = todo

    @api.multi
    def _compute_quality_alert_count(self):
        for picking in self:
            picking.quality_alert_count = len(picking.quality_alert_ids)

    @api.multi
    def check_quality(self):
        self.ensure_one()
        checks = self.check_ids.filtered(lambda check: check.quality_state == 'none')
        if checks:
            action = self.env.ref('quality.quality_check_action_small').read()[0]
            action['context'] = self.env.context
            action['res_id'] = checks.ids[0]
            return action
        return False

    @api.multi
    def _create_backorder(self, backorder_moves=[]):
        res = super(StockPicking, self)._create_backorder(backorder_moves=backorder_moves)
        if self.env.context.get('skip_check'):
            return res
        # remove quality check of unreceived product
        self.sudo().mapped('check_ids').filtered(lambda x: x.quality_state == 'none').unlink()
        res.mapped('move_lines')._create_quality_checks()
        return res

    @api.multi
    def action_done(self):
        vals={}
        # Do the check before transferring
        product_to_check = self.mapped('move_line_ids').filtered(lambda x: x.qty_done != 0).mapped('product_id')
        if self.mapped('check_ids').filtered(lambda x: x.quality_state == 'none' and x.product_id in product_to_check):
            raise UserError(_('You still need to do the quality checks!'))
        return self.action_dones()

    @api.multi
    def action_dones(self):
        """Changes picking state to done by processing the Stock Moves of the Picking

        Normally that happens when the button "Done" is pressed on a Picking view.
        @return: True
        """
        # TDE FIXME: remove decorator when migration the remaining
        todo_moves = self.mapped('move_lines').filtered(lambda self: self.state in ['draft', 'waiting', 'partially_available', 'assigned', 'confirmed'])
        # Check if there are ops not linked to moves yet
        for pick in self:
            # # Explode manually added packages
            # for ops in pick.move_line_ids.filtered(lambda x: not x.move_id and not x.product_id):
            #     for quant in ops.package_id.quant_ids: #Or use get_content for multiple levels
            #         self.move_line_ids.create({'product_id': quant.product_id.id,
            #                                    'package_id': quant.package_id.id,
            #                                    'result_package_id': ops.result_package_id,
            #                                    'lot_id': quant.lot_id.id,
            #                                    'owner_id': quant.owner_id.id,
            #                                    'product_uom_id': quant.product_id.uom_id.id,
            #                                    'product_qty': quant.qty,
            #                                    'qty_done': quant.qty,
            #                                    'location_id': quant.location_id.id, # Could be ops too
            #                                    'location_dest_id': ops.location_dest_id.id,
            #                                    'picking_id': pick.id
            #                                    }) # Might change first element
            # # Link existing moves or add moves when no one is related
            for ops in pick.move_line_ids.filtered(lambda x: not x.move_id):
                # Search move with this product
                moves = pick.move_lines.filtered(lambda x: x.product_id == ops.product_id)
                moves = sorted(moves, key=lambda m: m.quantity_done < m.product_qty, reverse=True)
                if moves:
                    ops.move_id = moves[0].id
                else:
                    new_move = self.env['stock.move'].create({
                                                    'name': _('New Move:') + ops.product_id.display_name,
                                                    'product_id': ops.product_id.id,
                                                    'product_uom_qty': ops.qty_done,
                                                    'product_uom': ops.product_uom_id.id,
                                                    'location_id': pick.location_id.id,
                                                    'location_dest_id': pick.location_dest_id.id,
                                                    'picking_id': pick.id,
                                                   })
                    ops.move_id = new_move.id
                    new_move._action_confirm()
                    todo_moves |= new_move
                    #'qty_done': ops.qty_done})
        todo_moves._action_done()
        self.write({'date_done': fields.Datetime.now()})
        return True

    @api.multi
    def my_other_logic(self, vals):
        my_trigger_field_value = vals.get('my_trigger_field_name')
        if my_trigger_field_value == 1:
            return super(StockPicking, self).do_transfer()

    @api.multi
    def action_cancel(self):
        res = super(StockPicking, self).action_cancel()
        self.sudo().mapped('check_ids').filtered(lambda x: x.quality_state == 'none').unlink()
        return res

    @api.multi
    def button_quality_alert(self):
        self.ensure_one()
        action = self.env.ref('quality.quality_alert_action_check').read()[0]
        action['views'] = [(False, 'form')]
        action['context'] = {
            'default_product_id': self.product_id.id,
            'default_product_tmpl_id': self.product_id.product_tmpl_id.id,
            'default_picking_id': self.id,
        }
        return action

    @api.multi
    def open_quality_alert_picking(self):
        self.ensure_one()
        action = self.env.ref('quality.quality_alert_action_check').read()[0]
        action['context'] = {
            'default_product_id': self.product_id.id,
            'default_product_tmpl_id': self.product_id.product_tmpl_id.id,
            'default_picking_id': self.id,
        }
        action['domain'] = [('id', 'in', self.quality_alert_ids.ids)]
        action['views'] = [(False, 'tree'),(False,'form')]
        if self.quality_alert_count == 1:
            action['views'] = [(False, 'form')]
            action['res_id'] = self.quality_alert_ids.id
        return action
