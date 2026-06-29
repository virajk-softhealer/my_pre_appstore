/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, onWillStart, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

class ShFreshdeskConfigurationAction extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            sync_contacts: true,
            sync_tickets: true,
            // import_conversations: false,
            // import_time_entries: false,
            manual_action: true,
            automated_action: false,
            from_date: "",
            to_date: "",
        });

        onWillStart(async () => {
            const data = await this.orm.call("res.config.settings", "get_freshdesk_flow_config", []);
            if (data.from_date) {
                data.from_date = this._toInputDateTime(data.from_date);
            }
            if (data.to_date) {
                data.to_date = this._toInputDateTime(data.to_date);
            }
            Object.assign(this.state, data || {});
            this.state.loading = false;
        });
    }

    _toInputDateTime(value) {
        // Convert "YYYY-MM-DD HH:MM:SS" to "YYYY-MM-DDTHH:MM"
        if (!value) {
            return "";
        }
        return value.replace(" ", "T").slice(0, 16);
    }

    _toServerDateTime(value) {
        // Convert "YYYY-MM-DDTHH:MM" to "YYYY-MM-DD HH:MM:SS"
        if (!value) {
            return false;
        }
        return `${value.replace("T", " ")}:00`;
    }

    onToggleMode(mode) {
        if (mode === "manual") {
            this.state.manual_action = true;
            this.state.automated_action = false;
        } else {
            this.state.manual_action = false;
            this.state.automated_action = true;
        }
    }

    async onSyncNow() {
        await this.orm.call("res.config.settings", "create", [{
            sh_enable_partner_sync: this.state.sync_contacts,
            sh_enable_ticket_sync: this.state.sync_tickets,
            // sh_import_conversations: this.state.import_conversations,
            // sh_import_time_entries: this.state.import_time_entries,
            sh_manual_action: true,
            sh_automated_action: false,
            sh_sync_start_date: this._toServerDateTime(this.state.from_date),
            sh_sync_end_date: this._toServerDateTime(this.state.to_date),
        }]).then(async (resId) => {
            await this.orm.call("res.config.settings", "action_sync_now", [[resId]]);
        });
    }

    async onSaveAuto() {
        await this.orm.call("res.config.settings", "create", [{
            sh_enable_partner_sync: this.state.sync_contacts,
            sh_enable_ticket_sync: this.state.sync_tickets,
            // sh_import_conversations: this.state.import_conversations,
            // sh_import_time_entries: this.state.import_time_entries,
            sh_manual_action: false,
            sh_automated_action: true,
        }]).then(async (resId) => {
            await this.orm.call("res.config.settings", "action_save_auto_setting", [[resId]]);
            this.notification.add("Auto sync settings saved.", { type: "success" });
        });
    }
}

ShFreshdeskConfigurationAction.template = "sh_freshdesk_connector.ShFreshdeskConfigurationAction";
registry.category("actions").add("sh_freshdesk_configuration_action", ShFreshdeskConfigurationAction);
