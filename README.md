# Osmia

A modular business app for companies that build and test electrical equipment, built on Django in the spirit of Odoo: separate modules
(Departments, Users, Tasks, Tools, Vendors, Parts, Stock, Orders, Assemblies, Budgets, Devices, Harness, Automations, Audit) that plug into a shared core and extend each other.

## Quick start (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python serve.py
```

Open http://127.0.0.1:8000. The first time, Osmia shows a **setup page**: make the administrator account, tick the
modules to install (all are ticked) and, to try Osmia out, **Load demo data** (demo users `alice`, `bob`, `carla` use
password `demo`). Osmia then restarts by itself to install the modules, which takes a few seconds.

`serve.py` runs Osmia with waitress. `--host 0.0.0.0` makes it reachable from other computers on the network; add their
name or address to `OSMIA_ALLOWED_HOSTS`. For real use, also set `OSMIA_DEBUG=0` and an `OSMIA_SECRET_KEY`:

```powershell
$env:OSMIA_DEBUG = "0"
$env:OSMIA_SECRET_KEY = "a long random text"
$env:OSMIA_ALLOWED_HOSTS = "osmia-pc,192.168.1.20"
.\.venv\Scripts\python serve.py --host 0.0.0.0 --port 8000
```

### Where the data is

Everything Osmia keeps is in one folder, `data\` (or `OSMIA_DATA_DIR`). That is the folder to back up:

| | |
|---|---|
| `data\db.sqlite3` | the database |
| `data\media\` | uploaded images |
| `data\backups\` | database backups, made before every module change (the last 20 are kept) |
| `data\addons\` | the installed modules, the versions they replaced, and the log of changes |

## Installing, updating and removing modules

The administrator does this on the **Modules** page (⚙ in the top bar):

- **Available** lists the modules that come with Osmia. Tick some and press **Install**; a module's dependencies are
  installed with it, and **with demo data** loads their demo data too.
- **Upload a module** installs a module `.zip` (see *Making a module* below). Uploading a newer version of an installed
  module updates it; an older version is refused (use **Roll back**).
- When the Osmia code is updated (`git pull`) and a module that comes with it has a new version, the module shows
  **Update to …** (or **Update all**).
- **Disable** hides a module but keeps its data; **Enable** brings it back. **Uninstall** removes it, and can delete its
  data too. A module that other modules need can't be disabled or removed before them.
- **Roll back** returns a module to the version an update replaced. It also puts back the database backup made just
  before that update, so everything changed since then is lost; the page says so first.
- **⬇ .zip** downloads an installed module, e.g. to install it on another Osmia.

Django can't add or remove modules while it runs, so each change is **queued** and Osmia restarts to apply it. On the
way up it backs up the database, moves the module files into place, runs the database migrations and checks that
Osmia starts. If any step fails, it puts the files, the module list and the database back as they were. The
**Recent changes** list shows what each change did, with the full log. One change waits at a time; **Cancel this
change** drops it before the restart.

Osmia restarts by itself under `serve.py` and under `manage.py runserver`. If a module stops Osmia from starting at all,
`serve.py` starts it again in **safe mode**, without modules, so the administrator can log in and disable that module on
the Modules page. Started some other way, set `OSMIA_SAFE_MODE=1` to do the same.

## Updating Osmia itself

Stop Osmia (Ctrl+C), then in the Osmia folder:

```powershell
# 1. Back up the data folder
$stamp = Get-Date -Format yyyyMMdd-HHmm
Copy-Item data "..\osmia-data-$stamp" -Recurse

# 2. Get the new version and its requirements
git pull
.\.venv\Scripts\python -m pip install -r requirements.txt

# 3. Start again; Osmia updates the database by itself
.\.venv\Scripts\python serve.py
```

Then open **Modules** and press **Update all** if modules have new versions.

- `git pull` needs Osmia to have been set up with `git clone https://github.com/Guidane/osmia.git`. If you downloaded a
  ZIP instead, unpack the new version over the old folder, keeping `data\` and `.venv\`.
- If `git pull` complains about local changes, you edited Osmia's files yourself. Run `git stash`, then `git pull`, then
  `git stash pop` to put your changes back, or ask whoever made them.
- Osmia 2 starts with a new, empty database: its database layout isn't the same as Osmia 1's.
- A browser may keep the old styles for a moment; reload with Ctrl+F5 if a page looks off.

## Developing Osmia

```powershell
$env:OSMIA_DEV_MODULES = "all"          # or e.g. "departments,tasks"
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py load_demo     # optional; --modules tasks,stock for some only
.\.venv\Scripts\python manage.py runserver
```

With `OSMIA_DEV_MODULES`, Osmia runs the modules straight from the source in `modules\`, so code changes show at once;
the Modules page is then read-only. Without it, Osmia runs the installed copies in `data\addons\modules\`.
Run the tests with `.\.venv\Scripts\python manage.py test` (it uses every module in `modules\`); `manage.py test tasks`
runs one module's.

## How the module system works

| Piece | Where | What it does |
|---|---|---|
| Manifest | `<module>/manifest.json` | Label, title, version, icon, depends, menu and sequence, like Odoo's `__manifest__.py` |
| Installer | `osmia/addons.py`, `core/module_views.py` | Installs, updates and removes modules (the Modules page); a change that fails is undone completely |
| Registry | `core/modules.py` | Finds installed modules, orders them by dependency, mounts each `<module>/urls.py` at `/<label>/` under the namespace `<label>` |
| Hooks | `core/hooks.py`, `<module>/hooks.py` | Extension points that let modules add to each other's pages without importing each other |
| Layout | `core/templates/base.html` | Top bar with the current module's menu and a module switcher, both built from the manifests |
| Demo data | `<module>/demo.py` | `manage.py load_demo` calls each module's `load()` in dependency order |

The modules that come with Osmia are in `modules/`. Users is built in (`users/`): it's the login, so it is always
there, and modules add to its pages through hooks. If a module's dependency is missing, startup stops with a clear
error.

### Built-in hooks

| Hook | Called by | Contributed by |
|---|---|---|
| `dashboard_widgets(request)` | Home dashboard | users, tasks, stock, assemblies, budgets |
| `user_detail_panels(request, user)` | User page | tasks (open tasks), stock (recent stock moves) |
| `task_detail_panels(request, task)` | Task page | stock (materials issued for the task), assemblies (linked assembly and stock shortfalls), budgets (budget the task is charged to) |
| `department_detail_panels(request, department)` | Department page | budgets (the department's budgets) |
| `task_costs(task_ids)` | Budgets | stock (cost of parts issued to each task) |
| `part_detail_panels(request, part)` | Part page | stock (where it is, moves), assemblies (assemblies that use the part), devices (connectors that use the part) |
| `assembly_detail_panels(request, assembly)` | Assembly page | devices (connectors of a "Device" assembly), tasks (tasks that automation rules created for it) |
| `device_detail_panels(request, device)` | Device page | harness (projects that use the device) |
| `order_detail_panels(request, order)` | Order page | tasks (tasks that automation rules created for the order) |
| `budget_costs(budget_ids)` | Budgets | orders (placed and received orders) |
| `budget_detail_panels(request, budget)` | Budget page | orders (the budget's orders) |
| `topbar_items(request)` | Top bar | automations (the 🔔 notifications bell) |
| `user_detail_facts(request, user)` | User page (details) | departments (the user's department) |
| `user_list_columns(request)`, `user_list_filters(request)` | User list | departments (Department column and filter) |
| `user_form_extensions(request, user)` | User form | departments (the Department field, for managers) |

## Modules

- **Departments** (`departments`, depends on users): the organisation as nested departments (e.g. Operations > Warehouse), as in Waggle V3. A department's page lists its members, including those in sub-departments, and other modules add to it (e.g. its budgets). Creating and editing departments needs the matching permission. A user's department is kept in this module (a membership) and shown on the user's page, list and form; managers assign it.
- **Users** (`users`, built in): custom user model with job title and phone. Pages for the user list, search, profile, create and edit. Members can edit only their own profile; managers need the `users.change_user` permission to edit others. Other modules add to these pages through hooks, e.g. Departments adds the department.
- **Tasks** (`tasks`, depends on users and departments): tasks have a department (the assignee's, if left blank). Views include a kanban board, filtered list, "My tasks", a Gantt timeline, status, priority, assignee, start/due dates and overdue highlighting. On the Gantt, a task without a start date runs from the day it was created (shown with a striped bar).
- **Parts** (`inventory`, depends on users and tools): the parts catalogue, following Waggle V3. Parts only describe things: a part number, a description, a nested category (e.g. Passives > Resistors) with its **attributes** (resistance, power, tolerance, ...), a unit and images. **Look up online** on the part form fills a part's attributes from its part number (see below); it never looks up prices. Under **Goes with**, a part lists the parts it **mates with** (both ways: a 9-pin plug mates with a 9-socket receptacle), the parts it **accepts** (one way: a D-sub housing accepts its crimp contacts; the contact's page shows the housing under **Fits into**) and the **tools** to work with it (e.g. the crimp tool for a contact). Quantities, locations and cost are in Stock.
- **Stock** (`stock`, depends on users, tasks and parts): how many of each part are where. Stock is kept **per location** (a part can be on several shelves; stock that hasn't been put away has no location). **Stock** lists every row with filters (location, low stock) and a search that also finds location codes; receipts, issues and counts (**New move**) each happen at a location, and you can't issue more than is there. Receipts at a price update the part's **average cost**, which issues are booked at, so tasks and budgets keep what they spent. A part's page shows its stock per location, its reorder level (**Reorder level**) and recent moves. **Moving stock:** on the Stock list, a location's page or a part's page, tick rows (or click them), pick a location and press **Move**; each move is logged as a **Transfer** (from → to) and joins any stock of the part already there.
- **Tools** (`tools`, depends on users): crimp tools, insertion tools, strippers, meters, ... with name, part number, type, manufacturer, asset tag, where it's kept, who looks after it, calibration date, notes and images. A tool's page lists the parts it's used with.
- **Vendors** (`vendors`, depends on users): the companies we buy from, with code, type (distributor, manufacturer, service provider), website, email, phone, address, our account number, VAT / tax ID, payment terms, currency, usual lead time, notes and their **contacts** (name, role, email, phone; edited in a table on the vendor form). The list searches names, codes, cities, account numbers and contacts. A vendor's page lists its orders. Inactive vendors are hidden from the list and can't be picked on new orders.
- **Assemblies** (`assemblies`, depends on parts, stock and tasks): an assembly has a type (generic, device, harness or plate stack), a status, a version, and build and usage instructions. Its bill of materials lists parts (added with the same part picker as orders) and/or sub-assemblies. The **revision** goes up automatically when the components or version change, and circular nesting is blocked. The assembly page shows the fully expanded structure, the total parts needed against stock on hand ("stock covers N builds"), where the assembly is used, and its tasks. A task can be linked to an assembly from the task page, or created with **New build task**. The link is stored in this module, so Tasks doesn't depend on Assemblies.
- **Budgets** (`budgets`, depends on users, departments and tasks): nested budgets (e.g. FY2026 > Operations > Warehouse Ops), each with a number, an amount and an optional department, as in Waggle V3. A task is charged to a budget of its own department, from the task page. Spending is whatever those tasks cost, collected from other modules through the `task_costs` hook. So far that means parts issued to the task, at the part's cost on the day of the move, plus the budget's placed and received orders (through `budget_costs`). Spending rolls up through sub-budgets. Pages show the amount, spent, remaining and a usage bar, and warn when a budget is overspent or its sub-budgets add up to more than it has. The budget list is a **tree table** (▸/▾ opens a budget's sub-budgets, with a filter box and expand/collapse all), and a budget's page shows its whole subtree the same way. The budget form has a **Sub-budgets** table to add, rename or re-amount the budgets directly under it (a new one without a department gets the parent's); a sub-budget can only be deleted there while it has no sub-budgets, tasks or orders of its own.

- **Devices** (`devices`, depends on users, inventory and assemblies): anything with connectors that takes part in an electrical setup. That covers units we build (a computer, an inverter, ...), test equipment we build to test them, and external equipment (power supplies, electronic loads, meters, ...). A device we build links to its assembly (type "Device") for the bill of materials. Each connector (J01, P01, ...) has a side, an optional physical connector part (its description is shown as the connector type in harnesses) and numbered pins, each with a label and a signal. Devices are created and edited **only here**: add connectors and assign or edit their pins (one at a time with **+ Add pin**, or several at once). Every change to a device's definition is kept as a new version, and an older version can be made current again. Pins have a **signal** from the shared list in **Devices › Signals** (the same for every device; renaming one renames it on every pin and in the harness signal rules), **tags**: a connector starts with one tag column and **+ Tag column** adds more, up to four (Tag 1–4). Tags are local to the connector: each tag column's dropdown offers the values used on that connector, and "+ Add new…" adds one (signals, in contrast, are global). The signal column comes after the tags; the signal list starts with common ones (GND, PWR, TxP/TxN, RxP/RxN, CAN_H/L, RS485_A/B, SDA/SCL, USB_D+/−, …) and a cable **set**: a set number plus straight, twisted, shielded or twisted shielded. Every column of a connector's pin table sorts (click again to reverse). A connector has a **function** (what it's for), a part (picking one shows the part's details, such as its attributes) and **pinout images**. **Clone** copies a connector with its pins. A device with the role **Interconnect** (adapter, breakout, extension) has a **pin mapping**: which input pin passes through to which output pin.
 **Import a pin table** (at the bottom of **Edit pins**) reads a CSV file with a header row (`Pin`, `Tag 1`–`Tag 4`, `Signal`, `Set`, `Set type`, any order): it updates pins with the same label and adds new ones, or replaces the whole table; unknown signals are added to the signal list. The format, written so an AI assistant can turn a datasheet pinout into an importable file, is in [devices/AI_README.md](devices/AI_README.md). **Remove** deletes a device, unless a harness project uses it (the page lists those projects).
- **Harness** (`harness`, depends on users and devices): the Wire Harness Designer from the stand-alone harness app. Scroll to zoom and drag empty space to pan; projects open centred on their harnesses, and **⌖ Center** centres the view again. Pick a device from the **Place device** dropdown and click the canvas to place it. Then click one connector and another (or **Loop Back**) to wire them pin to pin: each wire's two pins are chosen in the connect popup, and **Match remaining pins by signal** can pair the rest. The **signal rules** (e.g. PWR ↔ PWR, RX ↔ TX) flag wires that aren't allowed. Each harness gets its mating plug (J01 ↔ P01). Click a wire and then **Show pinout** to open its wire table in a popup; it shows each connector's part number, linked to its Part page, and type. Wires carry wire type, AWG, length and verified flags, and the table exports to CSV. Projects are versioned like devices. The designer only reads Osmia's devices and users: it can't create or change a device. **Open in Devices ↗** and **+ New device ↗** open the Devices module, and the designer picks up the changes when you come back to it (or with ↻). Clicking a connector shows a tip with **🔁 Loop back** and **➕ Create extension**: the extension is a new interconnect device (made in Devices) with exactly that connector's pinout, placed beside it with a harness in between. On an interconnect, mapped pins take the **tags** of the unit on the other side (shown as ↪ TAG in the pinout). The pinout shows each end's tag, the pins' **set** (from Devices) and a **wire colour**. **🧾 Order parts** lists a plug for each connector of the harness (remembering the part picked for it) and makes a draft order in Orders.
- **Orders** (`orders`, depends on users, parts, stock, budgets and vendors): purchase orders (PO-0001, ...) from a supplier (picked from Vendors), charged to a budget (picked in two steps: a **parent budget**, then one of its **sub-budgets**; leave the sub-budget empty to charge the parent itself), with lines of parts, quantities and unit prices. Lines are added from a **part picker** below them: search by number, description, category or attribute and press **Add to order** (again adds one more), or make a part on the fly with **+ Quick part** (just a part number and description; **New part ↗** opens the full form). An order goes draft → placed → (partly) received, or cancelled; only drafts can be edited, and an order needs lines to be placed. Placed and received orders count as spending on their budget. **Receive…** lists the open lines: tick the ones that came in and pick where each is put away (or **Set for all lines**). Ticked lines are booked into stock at that location and price; the rest stay open and the order shows **Partly received** until they come in. **Receive from a table** on that page fills the ticks from a pasted CSV (`Part number,Quantity,Location`, header optional, any column order): **Copy AI instructions** copies a prompt listing the order's open lines, to give an AI assistant along with the delivery note PDF; paste its table back and press **Read table**. Lines that came in full are ticked (at the location, if a code or path is given); short deliveries, extra quantities and parts not on the order are flagged. An order's page also has **Shipping documents**: photos or scans of delivery notes and packing slips.
- **Automations** (`automations`, depends on users): rules that do things by themselves. A rule is **When** (a trigger, e.g. "An order is placed") → **If** (optional conditions on the record, e.g. total is more than 500) → **Then** (steps, run in order). Steps are **Create tasks**, **Change a status** (of an order, task or assembly), **Notify people** (in-app, shown by the 🔔 in the top bar) and **Issue / receive stock**. Tasks a rule creates form a group, and **"All tasks created by a rule are done"** triggers a next rule, with `{source}` still pointing at the record the chain started from. The demo chains two rules this way: placing an order creates the check-in tasks, and finishing them receives the order into stock. If any step fails, the rule's other steps are undone (the change that triggered it stays), and the **Run log** shows what every rule did or why it failed. Rules can trigger rules at most 5 deep.

### Stock locations and codes (Stock module)

A location has a name ("Shelf A") and an optional short **label** ("A"). Its **code** joins the labels down the tree, so Rack row **A** › Rack **1** › Shelf **A** is **A1A**. Codes show next to locations everywhere, and the Stock search finds stock by its location's code. Labels are unique within the same parent.

**Generate locations** (on the Locations list, or **Generate sub-locations** on a location's page) builds a whole block at once. List the levels from the top down, each with a name, how many, the label style (A, B, C / a, b, c / 1, 2, 3 / 01, 02, 03) and an optional start (e.g. start racks at 7). The page previews the count and the codes before anything is made. 4 rack rows × 6 racks × 4 shelves gives 124 locations, A1A to D6D. Locations that already exist (same place, same label) are kept, so running it again with more racks only adds the new ones.

**Delete** on a location's page removes it together with its sub-locations, but only while no stock is kept in any of them. Otherwise it lists what to move first. Locations and categories are shown as a tree: ▸/▾ opens a level (what you leave open is remembered), **Expand all / Collapse all**, and the filter box (a name or a code like `B10`) shows the matches with the levels above them. Siblings sort naturally, so Rack 2 comes before Rack 10. The Locations table can delete empty locations too (✕ on each row), and its **Parts** and **Quantity** (total on hand) columns include sub-locations.

### Images

Parts, stock locations, assemblies, tasks, devices, harness projects and users can have images (e.g. a photo of a shelf or bin, so people can find it). Each location has its own page, showing the parts stored there, its sub-locations and its images. Add them with **+ Add images** on the record's page, or drop image files onto its Images card. Click a thumbnail to open it large. From there you can page through the images (← →), add a caption, **Make cover** or remove it. The first image is the record's cover, shown next to its name in lists (a user's first photo is their profile picture). Harness projects keep theirs on a page of their own, opened with **🖼 Images ↗** in the designer or from the project list.

Uploads are turned upright, scaled down to at most 2000 px, and re-encoded, which drops their metadata (camera, GPS position, ...). Each file can be up to 20 MB, as JPEG, PNG, GIF, WebP, BMP or TIFF. Files are stored under `data\media\` (or `OSMIA_MEDIA_ROOT`) and are served by Osmia to logged-in users only. Anyone logged in can add images to a record, except that only you and user managers can change your photos. To give another model images, add `images = GenericRelation('core.Image')` to it and put `{% load osmia_images %}{% image_gallery record %}` on its page.
- **Audit** (`audit`, depends on users): the change log of every module. Every create, change and delete of a module's records is logged automatically, with who made it and each field's old → new value (passwords only as "(changed)"). Child rows such as an order's lines or a device's pins are logged on their parent. Each module's menu has a **Log** link to its own log, and detail pages show a **History** panel. When one module's code changes another module's records, the entry gets a **⛓ chain tag** naming that module, the path it came through (e.g. tasks → automations → orders) and a **chain id**. Everything done in one go shares that id, and **Chains** lists every chain that crossed modules, with a trace page for each. **Activity** shows who changed how much.

### Looking parts up online

The part form's **Look up online** button asks Mouser for the part number and fills the part's attributes whose names
match (for example Manufacturer, Datasheet or Number of Positions). Values you already entered are kept unless you tick
"Replace values I already entered". Attributes the category doesn't have yet can be added to it with one click. Prices
and the description are never filled in.

It needs a free Mouser Search API key (mouser.com → Services → APIs), set before starting the server:

```powershell
$env:OSMIA_MOUSER_API_KEY = "your-key"
.\.venv\Scripts\python manage.py runserver
```

Other suppliers (DigiKey, Nexar, ...) can be added as providers in `inventory/lookup.py`.

### Importing the stand-alone harness app's data

```powershell
.\.venv\Scripts\python manage.py import_harness_devices "C:\path\to\harness"   # Devices module: the devices
.\.venv\Scripts\python manage.py import_harness "C:\path\to\harness"           # Harness module: projects, signal rules
```

The first command (Devices module) imports every device with all its versions and matches harness users to Osmia users by name; devices already in Osmia are skipped, so it's safe to repeat. The second (Harness module) imports every project and the signal rules. It never creates devices: it links each project to the devices already imported, and stops with a message if any are missing. Run it once, because running it again imports the projects a second time.

## Making a module

A module is a Django app with a `manifest.json` next to its `apps.py`:

```json
{
  "label": "crm",
  "title": "CRM",
  "version": "1.0.0",
  "osmia": ">=2.0",
  "description": "Customers and their contacts.",
  "icon": "🤝",
  "sequence": 60,
  "depends": ["users"],
  "requires": [],
  "menu": [
    {"label": "Customers", "url": "crm:list"},
    {"label": "New customer", "url": "crm:create"}
  ]
}
```

1. Make the folder `modules\crm`, run `python manage.py startapp crm modules\crm` and add the `manifest.json`.
2. In `crm/apps.py`, subclass `core.modules.OsmiaModuleConfig` (`name = 'crm'`); the manifest is read for you.
3. Add `crm/urls.py` (no `app_name` is needed; the namespace is the label).
4. Optionally add `crm/hooks.py` to contribute widgets or panels, and `crm/demo.py` (with a `load()`) for demo data.
5. With `$env:OSMIA_DEV_MODULES = "all"` set, run `manage.py makemigrations crm`.
6. Zip the `crm` folder and upload it on the Modules page, or try it straight from the source with `OSMIA_DEV_MODULES`.

`version` must go up for Osmia to offer an update. `osmia` is the Osmia versions the module works with. `requires`
lists Python packages it needs (e.g. `"requests>=2"`); they must already be installed on the server. The label can't
be the name of one of Osmia's own parts, or of a Python package such as `json`.

### Triggers and actions for automations

A module declares what rules can react to and do in its `hooks.py`, through `core/automation.py`, without depending on Automations:

```python
automation.event('orders.order_placed', 'An order is placed', fields=[Field('supplier', 'Supplier', lambda o: o.supplier)])
automation.emit('orders.order_placed', order)       # when it happens
automation.status_model(Order)                      # "Change a status" can set it (via Order.set_status if defined)

@automation.action('stock.stock_move', 'Issue / receive stock', params=[Param('part', 'Part', 'part', required=True), ...])
def stock_move(ctx, params):                        # ctx: object, source, rule
    ...
    return 'what was done, for the run log'
```

The rule builder offers every registered trigger and action, with a form for each action's parameters.

### Logging across modules

`core/audit.py` logs changes from model signals, so modules don't need to do anything; the Audit module stores the entries. A web request acts as the module whose page it is. Code that works on behalf of another module says so, and changes it makes in other modules get that module's chain tag:

```python
from core import audit

with audit.acting('automations', source='rule "Check in placed orders"'):
    ...
```

A model can opt out with `audit_log = False`, leave fields out with `audit_ignore = ('field',)`, and log its rows on a parent with `audit_record()`.
