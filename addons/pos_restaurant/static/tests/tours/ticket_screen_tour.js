import * as ProductScreenPos from "@point_of_sale/../tests/tours/utils/product_screen_util";
import * as ProductScreenResto from "@pos_restaurant/../tests/tours/utils/product_screen_util";
const ProductScreen = { ...ProductScreenPos, ...ProductScreenResto };
import * as Dialog from "@point_of_sale/../tests/tours/utils/dialog_util";
import * as FloorScreen from "@pos_restaurant/../tests/tours/utils/floor_screen_util";
import * as TicketScreen from "@point_of_sale/../tests/tours/utils/ticket_screen_util";
import * as Chrome from "@point_of_sale/../tests/tours/utils/chrome_util";
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("PosResTicketScreenTour", {
    test: true,
    steps: () =>
        [
            Dialog.confirm("Open session"),
            // New Ticket button should not be in the ticket screen if no table is selected.
            Chrome.clickMenuOption("Orders"),
            TicketScreen.noNewTicketButton(),
            TicketScreen.clickDiscard(),

            // Deleting the last order in the table brings back to floorscreen
            FloorScreen.clickTable("4"),
            ProductScreen.isShown(),
            Chrome.clickMenuOption("Orders"),
            TicketScreen.nthRowContains(2, "-0001"),
            TicketScreen.deleteOrder("-0001"),

            // Create 2 items in a table. From floorscreen, delete 1 item. Then select the other item.
            // Correct order and screen should be displayed and the BackToFloorButton is shown.
            FloorScreen.clickTable("2"),
            ProductScreen.addOrderline("Minute Maid", "1", "2"),
            ProductScreen.totalAmountIs("2.0"),
            Chrome.clickMenuOption("Orders"),
            TicketScreen.clickNewTicket(),
            ProductScreen.addOrderline("Coca-Cola", "2", "2"),
            ProductScreen.totalAmountIs("4.0"),
            ProductScreen.back(),
            FloorScreen.orderCountSyncedInTableIs("2", "3"),
            Chrome.clickMenuOption("Orders"),
            TicketScreen.deleteOrder("-0003"),
            Dialog.confirm(),
            TicketScreen.doubleClickOrder("-0002"),
            ProductScreen.isShown(),
            ProductScreen.totalAmountIs("2.0"),
            ProductScreen.back(),

            // Make sure that order is deleted properly.
            FloorScreen.clickTable("5"),
            ProductScreen.addOrderline("Minute Maid", "1", "3"),
            ProductScreen.totalAmountIs("3.0"),
            ProductScreen.back(),
            FloorScreen.orderCountSyncedInTableIs("5", "1"),
            Chrome.clickMenuOption("Orders"),
            TicketScreen.deleteOrder("-0004"),
            Dialog.confirm(),
            TicketScreen.clickDiscard(),
            FloorScreen.isShown(),
            FloorScreen.clickTable("5"),
            ProductScreen.orderIsEmpty(),
        ].flat(),
});

registry.category("web_tour.tours").add("OrderNumberConflictTour", {
    test: true,
    steps: () =>
        [
            FloorScreen.clickTable("3"),
            ProductScreen.isShown(),
            ProductScreen.addOrderline("Coca-Cola", "1", "3"),
            ProductScreen.back(),
            Chrome.clickMenuOption("Orders"),
            TicketScreen.nthColumnContains(1, 2, "Order"),
            TicketScreen.nthColumnContains(1, 3, "1"),
            TicketScreen.nthColumnContains(2, 2, "Self-Order"),
            TicketScreen.nthColumnContains(2, 3, "S"),
            TicketScreen.nthColumnContains(2, 3, "1"),
        ].flat(),
});
