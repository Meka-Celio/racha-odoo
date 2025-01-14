import { models } from "@web/../tests/web_test_helpers";

export class MailGuest extends models.ServerModel {
    _name = "mail.guest";

    _get_guest_from_context() {
        const guestId = this.env.cookie.get("dgid");
        return guestId ? this.search_read([["id", "=", guestId]])[0] : null;
    }

    /**
     * @param {Number[]} ids
     * @returns {Record<string, ModelRecord>}
     */
    _to_store(ids, store) {
        for (const guest of this.browse(ids)) {
            store.add("Persona", {
                id: guest.id,
                im_status: guest.im_status,
                name: guest.name,
                type: "guest",
                write_date: guest.write_date,
            });
        }
    }

    _set_auth_cookie(guestId) {
        this.env.cookie.set("dgid", guestId);
    }
}
