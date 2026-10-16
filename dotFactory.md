# Factory Shape — Specification

Status: **draft v1.21**, frozen for implementation planning. See the
[Changelog](#changelog) for version history.

Scope: the *execution shape* of the factory — modules, triggers, units, steps,
sinks, runners, ports, bindings and execution semantics. Out of scope: tenancy,
authentication, UI, persistence and deployment concerns.

---

## 1. Summary

A factory is a **scope** — a dataflow graph of triggers, steps and sinks — built
from **nested scopes**: the root, and the body of every composite unit. A step
invokes a **unit** (leaf or composite); a composite unit is itself a scope (a
cyclic dataflow graph of steps and sinks). Every connection is a **binding** — a
typed value flowing from a producer port to a consumer port. Loops and retries
are loop-carried (`prev`) bindings: a **unit** is a scope and need not loop,
whereas a **loop** is a unit (or the root) whose graph contains a `prev` cycle.
A unit declared with the **`loop`** keyword **must** contain a `prev` cycle; one
declared with `unit` need not.
Both are evaluated the same way — to quiescence as one firing — but only the
loop repeats. A scope is evaluated to quiescence as a single firing of the
node that invokes it, and only its boundary ports cross. The core is a pure
**interpreter**; everything with behaviour is a consumer-registered adapter.

## 2. Roles

| role | provides |
| --- | --- |
| **Core (factory)** | types & conformance, the graph, the scheduler, the `time` type, `terminated_at`, `attempt`, `abort`, and the composite (engine) runner |
| **Implementor** | the four extension points: trigger kinds, sink kinds, external ports, runner implementations |
| **Author** | data: types, units, runners, triggers, sinks, bindings |

The core interprets; it implements none of the behaviour-bearing pieces. A
*deployment* is a set of adapters plus authored data.

## 3. Extension points

Nothing with behaviour is built in. Consumers register adapters by name:

| point | selected by | contract |
| --- | --- | --- |
| **Trigger kind** | `kind:` on a trigger | fires; emits its declared `out` ports |
| **Sink kind** | `kind:` on a sink | consumes its `in` bindings; performs an external effect |
| **External port** | `external port` name | takes params, returns one output |
| **Runner implementation** | `kind:` on a runner | executes a leaf unit: consumes the runner's `in:`, produces its `out:` |

How an **external port** is evaluated — when it runs, whether its result is
memoized, and any per-Run state it keeps — is the implementation's concern, not
the spec's.

Because no sink kind is built in, the engine itself has **no notion of success**:
a Run either reaches quiescence (having performed some set of sink effects) or
aborts, and what a sink *means* is its adapter's concern. A Run's outcome is the
**set of sinks that fired**.

## 4. Types

Built-in types (only these):

```
text | int | float | bool | time | secret
```

Everything else is declared. `text` is a **Unicode string**, encoded as
**UTF-8** at every external boundary (a trigger seed, an external-port argument
or return, a sink input, `file` content). `int` is a **signed 64-bit integer** —
C's `long long`, two's complement — over the range −9,223,372,036,854,775,808 …
9,223,372,036,854,775,807. No value outside that range exists: a literal that
exceeds it is a load-time error, and any other out-of-range value — a seed, a
port, or an arithmetic result — is a **runtime error that aborts the Run**. An
`int` never wraps or saturates. At an external boundary it is a JSON number; one
outside the range is rejected like any other non-conforming value.
`float` is a **64-bit IEEE 754 binary value**, and always **finite**: `NaN` and
the infinities are not `float` values, and a boundary value that is not finite is
rejected. `time` is an **instant**, written as an
[RFC 3339](https://www.rfc-editor.org/rfc/rfc3339) timestamp and normalized to its
**canonical form**: UTC, exactly three fractional digits, `Z` offset —
`YYYY-MM-DDTHH:MM:SS.sssZ` (e.g. `2026-10-06T04:49:52.292Z`). Canonical `time`
strings sort lexicographically in chronological order, and equivalent instants
written with another offset or fractional precision compare equal.

```
type Issue   = { id: text, title: text, body: text?, labels: [text] }
type Commit  = { sha: text }
type Folder  = { path: text }
type Repo    = { name: text, url: text }
type Mcp     = { url: text }

type Severity = enum { low, medium, high }
type Finding  = { severity: Severity, location: text, description: text? }

type Verdict  = choice {
  succeeded { summary: text, findings: [Finding] }
  failed    { error: text }
  clarify   { question: text }
}
```

```ebnf
Type      = Primitive | Record | List | Optional | Enum | Choice | TypeRef ;
Primitive = "text" | "int" | "float" | "bool" | "time" | "secret" ;
Record    = "{" { field } "}" ;                 # fields newline- or comma-separated
field     = ident [ "?" ] ":" Type ;            # "?" = optional
List      = "[" Type "]" ;
Optional  = Type "?" ;
Enum      = "enum" "{" ident { ("," | NEWLINE) ident } "}" ;
Choice    = "choice" "{" { ident Record } "}" ; # tagged union
TypeRef   = ident ;
decl      = "type" ident "=" Type ;
```

**Conformance.** Type names are **aliases**, not nominal identities; conformance
is always **structural** (duck-typed).

- **Record (open):** a value offering *at least* the declared fields conforms;
  extra fields are allowed.
- **Enum / choice tag (closed):** membership and tag are exact — the only closed
  sets.
- **List / optional:** element-wise / absent-or-conforming.
- **`secret` (opaque):** conforms only to `secret`. `text` and `secret` do not
  inter-convert, and there is no secret literal — a secret enters only as data
  (§4.2).
- **`None` appears only under `?`.** A `None` value is legal only
  where the type is `T?` or the record field is declared `name?`. A `None`
  in any other position is a runtime error: trigger seeds are checked at
  admission, unit inputs at binding time, leaf/composite outputs at
  production — all abort before further code executes. Absence (`MISSING`)
  is separate from `None` and follows the firing rule (§10); §4.1 defines the
  two, and §8 defines how each behaves in expressions.

### 4.1 `None` and absence

Two different things can be unstated, and the spec keeps them apart:

- **Absent (`MISSING`)** is *not a value*. Evaluating a path yields it when the
  port or field it names has no value on this Run — a producer that has not
  fired, or an optional (`?`) port/field left unproduced. `MISSING` **never
  crosses a binding**: when a binding's source evaluates to `MISSING` the
  binding is dropped or the node is disabled (§10), so no consumer ever receives
  it as a value. It exists only for the duration of evaluating an expression (a
  binding source, a guard, a `when`).
- **`None`** *is a value*. It is legal only in a `?` position — a `T?` port, or a
  record field declared `name?` — and it crosses bindings like any other value.
  There is no `None` literal in the DSL: `None` enters the factory only as data,
  through a trigger seed, an external-port return, or a leaf/composite output
  (§11).

So `T?` denotes the set *{ a `T`, `None` }*, and absence is a separate axis: a
port either produced a value (possibly `None`) or it did not.

An **external boundary renders the two the same.** When a sink or adapter must
serialize an input that was dropped as optional (absent), it presents it as
`None` — the `file` sink receives an absent optional input as `None` (§10). A
consumer of the resulting artifact therefore cannot distinguish a `None` value
from a dropped optional; that conflation is the adapter's concern, not the
language's. (It is also why a sink with inputs must have at least one required
input, §11.)

### 4.2 Secrets

`secret` is a built-in primitive: an **opaque string whose content is
sensitive**. Underneath it **shares `text`'s representation** — it is carried as
a UTF-8 string at every boundary (a seed, a binding, an external-port argument
or return, a sink input) — and `secret` adds only *semantics* (redaction and
opacity), not a distinct wire form. It has **no literal** — a secret is never
written in `.fct`, where it would enter the factory source and its digest. Like
`None` (§4.1), a secret enters the factory only as **data**: a trigger seed, an
external-port return, or a leaf/composite output. It may be declared in any type
position — `secret`, `secret?`, `[secret]`, or a record field `token: secret`.

**Redaction is an observation rule.** Wherever the factory *reports* a value —
the Run's event log and store, a trace, an error message, a status page, the web
UI — a secret is rendered as a fixed marker of exactly **eight** characters
(`"********"`), never its content. The marker's length is **independent of the
secret's**, so a redacted value never leaks how long the secret was.
Redaction is **transitive through containers**: a secret nested in a record or a
list is redacted at that leaf, so a structure may be reported whole while its
secrets are hidden. Redaction is **not** a value-flow rule: **every receiver gets
the secret as a secret**, never a bare value — an external-port argument, a
leaf's inputs, a sink input. The receiver therefore knows the value is sensitive,
and unwraps it to *use* it; that is the adapter's concern. What a receiver
*reports* is redacted: a generic sink (the `file` sink) writes the marker, and
only a boundary that deliberately opts in ever sees the content.

**A secret is opaque in expressions.** It may be bound, transported and passed,
but no operator may be applied to it. Truthiness (a guard, a sink's `when`,
`not`, `and`/`or`), equality (`==`/`!=`), ordering, arithmetic and `is` are all
runtime errors on a secret (§8). A secret therefore cannot gate a node, and it
cannot be used to compute another value — there is no expression whose result
depends on a secret's content except passing it on unchanged.

**No widening, no reveal.** Despite the shared representation, `secret`
conforms only to `secret`: it is not `text`, `text` is not `secret`, and no
operator, cast or built-in converts between them. This keeps a secret from being interpolated into a command, a
path or a message by accident, and from drifting into an observation as text.

## 5. Implicit ports

Every **trigger and step** that fires exposes **`terminated_at: time`**, the
canonical instant of its **most recent** firing, and **`attempt: int`**, the
1-based ordinal of that firing (1 on the node's first firing, 2 on its next, …).
A node that is skipped (§10) does not fire, so it has neither; a node that
re-fires exposes the instant and ordinal of its latest firing. A trigger fires
exactly once, at admission (§10), so its `terminated_at` is the admission
instant and its `attempt` is 1. Sinks fire but are not binding sources, so a
sink's `terminated_at` / `attempt` are not referenced.

`terminated_at` and `attempt` are both information and sequencing tokens: binding
either forces one node to wait for another (see §10).

Identity metadata is exposed as the reserved source **`this`**, resolved
relative to the step whose binding is being evaluated:

- **`this.metadata.run.id: text`** — the Run's identity. Assigned once when the
  Run is created (by the host, or by the engine for standalone runs); constant
  for the whole Run, including re-fires.
- **`this.metadata.node.id: text`** — the identity of the **node** being executed
  (the trigger, step or sink). Stable across that node's re-fires, so it can key
  per-node (e.g. per-loop) state; distinct for distinct nodes.
- **`this.metadata.step.id: text`** — the current execution's identity. New for
  every step (every execution of a node); a node that re-fires has a different
  step id each time.

All three ids are **opaque** — factories must not parse them. Every node
execution — trigger, step or sink — is a step and carries its own `step.id`.

## 6. Runners

A **runner** is a named definition that selects a registered **implementation**
(an extension point) and declares that implementation's **interface**. A runner
block carries **only** these fields — `kind` (final), `extends?`, `in`, `out`,
`config`, and the fields its kind declares:

- **`in:`** / **`out:`** — the runner's **interface**: the types the
  implementation **consumes** and **produces** at run time. The implementation
  reads and writes the interface's fields by name; the types are the only place
  those names appear. **Neither is mandatory** — a runner may declare `in:` only,
  `out:` only, both, or neither:
  - an **agent** declares `in:` only: it *generates* its output from everything
    it receives, so the output is the **leaf's** `out:`, not the runner's;
  - a runner with a **fixed** output shape declares `out:` too;
  - a **generic** runner (e.g. `docker`) declares neither, and each leaf declares
    its own ports.
- **`config:`** — the runner's **static configuration**: a value whose type is
  named for it (`config: DockerEnv = { … }`), holding values fixed before the
  Run (an image, a region).
- **its kind's fields** — the knobs the implementation declares (for `docker`,
  `image` / `cpu` / `region` / …, and the leaf's `command`). A kind's field may
  carry its own type, just like `config`.

`prompt` and `auth` are declared by no kind and are not fields: an agent's
instruction is `prompt: text` in the runner's `in:` interface, and its
credential is a `secret` field of it (§6.3).

```
# agents.fct — a runner and its interface are declared together
type AgentCall = { token: secret, prompt: text }

runner agent:                       # an agent declares `in:` only …
  kind: opencode
  in:   AgentCall
  # … its output is the leaf's `out:`, generated from the inputs
```

A factory that reuses the runner **imports** the module, so the interface comes
with it — the author binds the interface's fields and never restates the type:

```
# factory.fct
import "agents" as agents

step draft:
  runner: agents::agent
  in:                                   # conforms to AgentCall
    token:  credential(key: "opencode") # secret
    prompt: file "prompts/draft.md"     # text
  out: { draft: text }
```

Static configuration is a typed record, not free-form fields:

```
type DockerEnv = {
  image:   text
  cpu:     int?
  network: enum { none, host, bridge }?
  workdir: text?
  region:  text?
}

runner platform:                    # provided by the platform — the root
  kind:   docker
  config: DockerEnv = { image: "factory-vm:base", cpu: 2, network: none }

runner builder:
  extends: platform
  config:  DockerEnv = { image: "factory-vm:builder" }   # records merge
```

Definitions are not versioned in the source: the factory is code under version
control, and the runtime records a **content digest** of each resolved
definition (see §11).

- `kind:` is **implicitly final** — a runner cannot change its implementation.
- The platform provides the root runner; deployments extend it.
- `config` is a typed, checked value; `in` / `out` are the interface a leaf unit
  conforms to (§7).

### 6.1 Field modifiers

Fields of any definition carry a modifier:

| modifier | meaning |
| --- | --- |
| `name!` | **required** — a concrete definition must set it |
| `name?` | **optional** (explicit) |
| `final name` | **private** — inherited, but not overridable |
| `name` | optional, overridable |

A bare field is optional and overridable. A `final` field with no value is a
private default inherited by descendants but not overridable.

### 6.2 Inheritance (`extends`)

Any named definition may `extends` **at most one** other definition of the
**same kind** (runner←runner, unit←unit, sink←sink, trigger←trigger).

- **Scalars** are overridden by the child.
- **Lists** are merged (parent then child).
- **Records** are merged field-by-field: the child's fields override, the
  parent's other fields are kept.
- **`final`** fields may not be overridden.
- **Required (`!`)** fields must be satisfied by a concrete definition.

A definition that leaves a required (`!`) field unset is a **template** (an
abstract base); a descendant — or the deployment — must satisfy it before the
definition is used.

```
type DockerEnv = { image: text, region: text, mounts: [text]?, cpu: int? }

runner base:
  kind:   docker
  config: DockerEnv = { image: "factory-vm:base", region: "eu-west-1",
                        mounts: ["/cache"] }

runner eu:
  extends: base
  config:  DockerEnv = { mounts: ["/cache/deps"] }
  # image and region are inherited; mounts merge -> ["/cache", "/cache/deps"]

runner eu_big:
  extends: eu
  config:  DockerEnv = { cpu: 8, mounts: ["/data"] }   # a new scalar; lists merge

runner us:
  extends: eu
  config:  DockerEnv = { region: "us-east-1" }         # a scalar overridden
  # mounts is still ["/cache", "/cache/deps"]
```

A runner's `config` value is merged by the same rules — records field-by-field,
lists concatenated — and must conform to its declared type after merging.

### 6.3 The interface is closed

Every block is **closed**: it carries the fields the spec defines, the fields its
**kind** declares, and no others. A name that is neither — a misspelling, or a
field some other kind uses — is a **load-time error**, never silently ignored.

- A runner's `in:` / `out:` are types; its `config` and its kind's fields must
  conform to the types named for them.
- **A leaf provides its runner's interface.** When a runner declares `in:` (or
  `out:`), a leaf naming it binds every required interface field, and may bind
  further inputs (the implementation receives them as data). A runner that
  declares neither is **generic**: each leaf declares its own ports.
- A trigger's and a sink's config fields are the ones *their* kind declares.
- `prompt` and `auth` are declared by no kind. An agent's instruction is
  `prompt: text` in the runner's `in:` interface, bound by the step; its
  credential is `token: secret` there.

## 7. Units & steps

A **unit** is a named, runnable definition. It is the unit of **reuse and
import**.

- **`runner:`** → *leaf* unit, executed by a runner's implementation.
- **nested `step:` / `sink:` entries** → *composite* unit — a **scope**: a
  workflow (and, optionally, its effects), executed by the core engine.
- **`loop:`** declares a composite unit that **must** contain a `prev` cycle
  (§10.1); `unit:` may or may not loop. Otherwise the two are identical.

A **step** is an **invocation** inside a composite. It is one of:

- a **reference** — `use: name` plus `in:` bindings; the step **inherits**
  the unit's `out`; or
- an **inline unit** — an anonymous definition (a `runner:` or nested `step:`s +
  `in:` + `out:`) used once at that step.

Name a unit only when it is reused or imported; otherwise inline its payload at
the step.

```
unit planner:                    # leaf, named because it is reused
  runner: platform
  in:  { issue: Issue }
  out: { plan: Plan }

unit review:
  in:  { issue: Issue }
  out:
    plan: Plan = plan.plan
    result: text = lint.result

  step plan:                     # reference: out inherited from planner
    use: planner
    in:  { issue: in.issue }

  step lint:                     # inline unit: declares its own out
    runner: platform
    in:  { plan: plan.plan, command: "make lint" }
    out: { result: text }
```

Rules:

- **Every unit declares `in` / `out`** — leaf and composite alike. Ports are
  required unless marked `?`; optional inputs may be omitted and optional outputs
  may be absent on a given Run.
- **Composite outputs coalesce.** A composite's `out:` may repeat a port, each
  binding with an optional `if` guard; the port takes the value of a binding
  whose guard holds and whose source is present (a later binding wins on
  overlap). That is how disjoint branches — a recursion's base case versus its
  recursive call — select the result.
- **A unit names a runner** (`runner: name`) — required for leaf units; a
  composite has no runner (the engine is its runner).
- A **referenced** step declares only `in:` bindings and **inherits** the unit's
  `out` — same ports, same names. It cannot rename or subset them; to reshape
  outputs, wrap the call in an inline composite (as `unit review` above does):

  ```
  step p:
    in: { issue: in.issue }
    out: { the_plan: Plan = inner.plan }   # expose only `the_plan`
    step inner:
      use: planner
      in: { issue: in.issue }
  ```
- An **inline** step declares `in:` bindings and its own `out:` types. Input
  types are inferred from the bound sources; a binding may optionally declare an
  upper-bound type (`name: Type = source`). A binding is **required** unless
  marked `?` (`name?: source`), which makes an absent source drop the binding
  without gating the node. A node's bindings may not **all** be optional: at
  least one must be required (§11).
- **A leaf provides its runner's interface.** When the runner declares `in:` /
  `out:` (§6), the leaf binds every required field; a **generic** runner declares
  neither, and the leaf declares its own ports (§6.3).
- **Closed.** A leaf's fields are the ones its runner's kind declares, and a step
  binds only ports its unit declares. An undeclared name is a load-time error;
  nothing is silently ignored (§6.3).
- **`use:`** references any unit by name. Leaf and composite references are
  identical in form.
- **A unit may invoke itself.** `use:` may name the enclosing unit, directly or
  transitively: recursion is ordinary composite invocation. It must terminate
  through guards (a base-case branch) or the max-firings bound; the spec defines
  no separate recursion limit.
- **A composite is a scope.** Its body is a cyclic dataflow of steps and sinks;
  the engine evaluates it to quiescence as a single firing of the invoking step,
  so only the unit's `out` ports cross the boundary (§10). A `prev` cycle in the
  body makes the unit a **loop**; it still fires once and yields one result.
- **Capabilities are ordinary ports.** Folders, MCP servers and network access
  are just (usually optional) ports; how a runner provisions their values is the
  implementation's concern, not the spec's.
- **`in.` / `out.`** reference the enclosing composite's boundary ports.

## 8. Declarations & bindings

```
external port checkout(issue: Issue)    -> Folder
external port github_mcp()              -> Mcp
external port loop_count(task_id: text) -> int

trigger on_issue:
  kind: linear
  board: "ENG"
  out: { issue: Issue }

step run:                          # the root invocation of the workflow
  runner: platform
  in:
    command: "factory-run"
    issue: on_issue.issue
    repo:  checkout(issue: on_issue.issue)         # external port -> Folder
    mcp:   github_mcp()                            # external port -> Mcp
    round: loop_count(task_id: on_issue.issue.id)  # external port -> int
  out: { verdict: Verdict }

sink notify_ci:
  kind: webhook
  in:  { verdict: run.verdict }
  when: run.verdict is succeeded

sink on_abort:
  kind: notify
  in:  { reasons: abort.reasons }
```

Block fields:

| block | fields |
| --- | --- |
| `runner` | `kind` (final), `extends?`, `in` / `out` (a type — the implementation's interface), `config` (a typed value), and the fields its kind declares |
| `trigger` | `kind`, `extends?`, `out` (types), and the config fields its kind declares |
| `sink` | `kind`, `extends?`, `in` (bindings), `when` (exit condition), and the config fields its kind declares |
| `unit` | `runner` **xor** a composite (nested `step` / `sink` entries), `extends?`, `in` (types), `out` (types, or type = binding for a composite), and the runner's kind fields |
| `step` | `use` (reference) **xor** an inline unit (`runner` or nested `step`s / `sink`s, `in` (bindings), `out` (types)) — valid at the root and inside a composite |

```ebnf
module        = { import } { declaration } ;
import        = "import" STRING "as" ident ;
declaration   = type_decl | external_port | runner | trigger | sink | unit | loop | step ;

type_decl     = "type" ident "=" Type ;
external_port = "external" "port" ident "(" [ params ] ")" "->" Type ;
params        = param { "," param } ;
param         = ident ":" Type ;

runner        = "runner" ident ":" block ;     # kind + in/out interface + config
trigger       = "trigger" ident ":" block ;
sink          = "sink" ident ":" block ;
unit          = "unit" ident ":" block ;       # leaf if runner:, composite if nested step:/sink: entries
loop          = "loop" ident ":" block ;       # a composite unit that must contain a `prev` cycle
step          = "step" ident ":" block ;       # use: (reference) xor inline unit
block         = { entry } ;                    # indentation-scoped
entry         = field | extends | step | sink ;
field         = [ "final" ] ident [ "!" | "?" ] ":" [ Type "=" ] [ value ] ;
extends       = "extends" qual ;

in            = "in" ":" ( types | bindings ) ;
out           = "out" ":" ( types | out_bindings ) ;
# within a runner block, `in:` / `out:` hold a single Type — the
# implementation's interface: iface = ( "in" | "out" ) ":" Type ;
when          = "when" ":" expr ;
types         = map( ident [ "?" ] ":" Type ) ;
bindings      = map( binding ) ;
binding       = [ "prev" ] ident [ "?" ] ":" [ Type "=" ] source [ "if" expr ] ;
              # `prev` marks a loop-carried (feedback) edge (§10.1); `Type =` is
              # an optional upper bound on the bound value. `prev` is a reserved
              # word: a port or binding may not be named `prev`.
out_bindings  = map( ident [ "?" ] ":" Type "=" source [ "if" expr ] ) ;  # names may repeat (coalesce)
map(X)        = "{" [ X { ("," | NEWLINE) X } ] "}"      # inline
              | NEWLINE INDENT { X NEWLINE } DEDENT ;    # block

qual          = [ ident "::" ] ident ;
# A value may be arithmetic; operators are whitespace-delimited (§8 notes).
value         = additive ;          # config data: literal / file / list / record
literal       = STRING | NUMBER | "true" | "false" | WORD ;
              # a `time` literal is a STRING in RFC 3339 form (§4); `-1` is a NUMBER
list          = "[" [ value { "," value } ] "]"
              | NEWLINE INDENT { "-" value NEWLINE } DEDENT ;
record        = "{" [ vfield { ("," | NEWLINE) vfield } ] "}"
              | NEWLINE INDENT { vfield NEWLINE } DEDENT ;
vfield        = ident ":" [ Type "=" ] value ;

source        = additive ;          # binding source: a value, plus qual / path / call
path          = [ ident "::" ] ( ident | "in" | "out" | "abort" | "this" ) { "." ( ident | NUMBER ) } ;
call          = qual "(" [ ident ":" source { "," ident ":" source } ] ")" ;

# Arithmetic, shared by values and guards: left-associative, `* / %` bind
# tighter than `+ -`, unary `-` desugars to `0 - x`.
additive       = multiplicative { ("+" | "-") multiplicative } ;
multiplicative = unary { ("*" | "/" | "%") unary } ;
unary          = [ "-" ] primary ;
primary        = literal | "file" STRING | list | record | "(" expr ")"
               | qual | path | call ;   # qual/path/call: only in a `source`
```

Guard expressions (`if`, `when`, `is`):

```ebnf
expr    = or ;
or      = and { "or" and } ;
and     = not { "and" not } ;
not     = [ "not" ] test ;
test    = additive [ cmp additive | "is" ident ] ;
cmp     = "<" | ">" | "<=" | ">=" | "==" | "!=" ;
```

**Reference semantics.** Except where this spec says otherwise, the semantics of
values and expressions follow **Python 3** — numeric comparison across `int` and
`float`, the sign of `%`, and so on. Each deviation is stated where it applies:
the 64-bit `int`, an out-of-range or non-finite result aborting, no implicit
`int`→`float` conversion, and a boolean context requiring a `bool` (below).

Arithmetic operators (`+ - * / %`) are **numeric** (`int` / `float`),
left-associative, with `* / %` binding tighter than `+ -` and unary `-`
negating. `+ - *` yield `int` for two `int`s, else `float`; `/` is true division
(always `float`); `%` is remainder, taking the **sign of the divisor** (as in
Python). Operators must be **whitespace-delimited**: `a + b` is arithmetic, while
`a-b`, `factory-vm:base` and `src/**` stay bare words. Arithmetic is allowed in
guards and in values (binding sources, `out:` bindings, config); the engine
evaluates it wherever it evaluates a value (binding and guard positions).

**Integer arithmetic is 64-bit, exact, then range-checked.** `+`, `-` and `*` on
two `int`s — and unary `-`, which desugars to `0 - x` — are computed exactly and
the result checked against the 64-bit range (§4). A value above the maximum
(**overflow**) or below the minimum (**underflow**) is a runtime error that
aborts the Run; an `int` never wraps or saturates. A literal counts its sign
toward the check, so the minimum is writable (`-9223372036854775808`) while
`9223372036854775808` is a load-time error. `/` is true division, so it yields a
`float`, never an out-of-range `int`.

**Float arithmetic stays finite.** A `float` result must be finite: arithmetic
that would **overflow** to an infinity, or **underflow** a nonzero result to
zero, is a runtime error that aborts the Run — exactly like an `int` out of range
(§4). A `float` is never `NaN` or infinite. Rounding a result to the nearest
`float` (including to a subnormal) is not an error.

**A bad operand aborts the Run.** A non-numeric or **absent** operand, or `/` or
`%` by zero (`int` and `float` alike), is a runtime error that aborts the Run
(§4).

Comparisons: `==` / `!=` are **structural** equality, defined for any values
(except a `secret`, §4.2).
Ordering (`<` `<=` `>` `>=`) is defined for `int`, `float`, `time` and `text` —
numeric, chronological and lexicographic order (canonical `time` strings sort
chronologically). Numeric equality and ordering compare by value across `int`
and `float` (`1 == 1.0`, `1 < 1.5`), as in Python. `is <tag>` tests a `choice`
value: true when the value is a single-key record named `<tag>`, false otherwise.

**Numeric types do not implicitly convert.** An `int` does not conform to a
declared `float`, and a `float` does not narrow to `int`; to place an `int` where
a `float` is expected, write a `float`. Operators are the one exception: a mixed
`int`/`float` pair is allowed in arithmetic (yielding a `float`) and in
comparison (comparing numerically).

**A `secret` is opaque.** It supports **no** operator (§4.2). Applying
truthiness — a guard, a sink's `when`, `not`, `and`/`or` — equality (`==`/`!=`),
an ordering, arithmetic, or `is` to a secret is a runtime error that aborts the
Run. A secret may still be bound and passed unchanged; it is only *computed
with* that is forbidden.

**Absence and `None` in expressions.** A guard may read a port that is absent on
this Run (§10), or a `?` port that holds `None` (§4.1). Both read as "no value"
in a boolean context, but they are not the same thing: absence (`MISSING`) is
the non-firing of a producer and never crosses a binding, while `None` is a
value that did. The operators treat them as follows:

- **Boolean context.** A boolean context — a guard, a sink's `when`, `not`, or
  an `and`/`or` operand — takes a **`bool`**. An absent value and a `None` are
  **falsy** there (so `not x` is true when `x` is absent or `None`); any other
  present value is a runtime error. There is no truthiness of a number or a
  string: write the test explicitly (`x != 0`, `x != ""`), which is always
  possible without loss of generality.
- **Equality.** `==` / `!=` are total and structural. Two absent values are
  equal and two `None`s are equal; an absent value is unequal to every present
  value, and a `None` is unequal to every non-`None` present value. Because one
  is a value and the other is not, **a `None` and an absence are unequal to each
  other** (`None == MISSING` is false).
- **Ordering.** `<` `<=` `>` `>=` are **false** if either operand is absent or
  `None` (so `<` and `>=` may both be false).
- **`is <tag>`.** False for an absent value or a `None`.
- **Arithmetic.** `+ - * / %` are defined only for **present** numeric operands:
  an absent or `None` operand is a runtime error that aborts the Run, exactly
  like a non-numeric operand (§4).

A guard that is absent, `None`, or false does not enable its node: a present
source with a false guard disables the node, and an absent source drops the
binding (§10).

Lexical rules:

- Comments run from `#` to the end of the line (outside strings).
- Indentation (spaces) is significant; a block is an indented run of entries.
- A `:` is a **field separator** only when followed by whitespace or the end of
  the line. Any other `:` belongs to a bare word — that is what keeps
  `factory-vm:base` and `i::JiraTicket` intact.
- `file "<path>"` reads a file relative to the module, wherever a value is
  accepted.
- Values may be **structured**: records (`{ … }` or an indented block) and lists
  (`[…]` or indented `-` items), nested freely.
- A field or record entry may be **typed**: `name: SomeType = value`. The value
  must **conform structurally** to the type (checked at load). Types therefore
  describe config values as well as ports — that is how authors name their own
  abstractions.
- A **bare token** runs to the next whitespace or one of `{}[](),=?!<>"#` (a `:`
  followed by a non-space stays in the token, per above). A bare token that reads
  as a decimal numeral is a `NUMBER` (`0`, `-1`, `1.5`, `2e3`); `true` / `false`
  are booleans; every other bare token is a `WORD` (`docker`, `ro`, `none`,
  `factory-vm:base`).
- **Structural keys are reserved** inside a block: `kind`, `extends`, `runner`,
  `use`, `in`, `out`, `when`, `config`, `final` and `step` are interpreted
  structurally and cannot be used as config field names. The binding marker
  **`prev`** is reserved too: a port or binding may not be named `prev` (§10.1).

Notes:

- **Every block is closed (§6.3).** A block's fields are the spec-defined ones
  plus those its kind declares; an unknown or misspelled field is a load-time
  error, never silently ignored. `in` / `out` (a runner's interface) and
  `config` (a runner's static configuration) are structural keys.
- **The root holds Trigger, Step and Sink; a composite body holds Step and
  Sink.** Triggers are root-only sources; steps are invocations; sinks are
  effects and may appear in any scope. A composite unit is invoked by a step.
- **Reusable boundaries** (`unit`, `runner`, `external port`) declare types;
  **local bindings** (`step.in`, `sink.in`) may infer, or declare an upper bound
  with `name: Type = source`.
- **A binding has exactly one source.** The same value may be bound by several
  consumers (fan-out); there is no multi-writer port.
- **Config is literal data.** A `value` (config, a record field, runner/trigger
  config) is a literal, `file`, list, record or arithmetic over those — never a
  `qual`, `path` or external-port `call`. Those appear only as a binding
  **source**.
- **A `choice` value is a single-key record** `{ tag: { …fields } }`, tested with
  `is <tag>` (§4). A binding source is a path or call, not a literal record, so
  choice values cross bindings as data emitted by a node rather than authored
  inline.
- **Empty values** are allowed: `region!:`, `final endpoint:` — a required or
  final field with no default.

## 9. Modules & files

A factory is a set of **modules** (files). The loader is given one **root
module**; its transitive imports form the factory.

### 9.1 Imports

Imports are **explicit and namespaced** — nothing merges implicitly.

```
# factory.fct (root)
import "agents"  as agents
import "runners" as runners

unit review:                        # local declaration
  step implement:
    use: agents::coder              # qualified reference
```

- `import "<path>" as <alias>` binds an alias for the module's declarations.
- A declaration is referenced as `<alias>::<name>`; declarations in the current
  module are unqualified.
- Within a module, declaration names and aliases must be **unique**, and an
  alias may not collide with a local declaration name.
- Paths are resolved **relative to the importing file**, and the `.fct`
  extension is implied: `import "types"` reads `types.fct`.
- Import cycles are allowed: resolution is by name, not load order.

### 9.2 External content

Large literals may live in their own files.

```
step coder:
  runner: runners::builder
  in:  { prompt: file "prompts/coder.md" }
  out: { commit: Commit }

step run_tests:
  runner: runners::builder
  in:  { script: file "scripts/tests.sh" }
  out: { result: GateResult }
```

## 10. Execution & concurrency

**Scopes.** Execution is defined over **scopes**. A scope is a dataflow graph
with boundary ports: the **root** (seeded by its trigger; no `in`), or the body
of a **composite** step (its `in` / `out` ports). A scope's nodes are its steps
and sinks. A scope need not loop — but when its graph contains a `prev` cycle
(§10.1) it is a **loop**. A `loop` declaration must contain one; a `unit`
declaration need not. Every scope, looping or not, is evaluated the same
way: to quiescence, as one firing of the node that invokes it.

**A scope runs to quiescence as one firing.** A scope is evaluated once per
firing of the node that invokes it: the root once per Run (when its trigger
fires), a composite step once per firing of that step. Evaluation fires the
scope's nodes until none is enabled. **Only the boundary ports cross**: the
firings of nodes inside a scope are not visible outside it, so invoking a
composite step is a *single* firing that yields the scope's `out` values once.
A node's dependencies are its **sibling nodes within the same scope** (below); a
binding may not name a node in another scope. This is what makes an effect
*inside* a loop's scope fire on every pass, while an effect *downstream of the
scope* fires once per invocation of that scope (here, once — §10.2).

- **Triggers fire once, at admission.** A Run begins when its trigger fires: the
  host supplies the trigger's `out` values (the **seed**), which are checked at
  admission (§4, §11) and then held constant for the whole Run. The trigger
  exposes those values and its `terminated_at` (the admission instant); it fires
  once, before the steps, and never re-fires. For a trigger to fire again the
  host starts a **new Run** — repetition across Runs is the listener's concern
  (§12), not this execution model's.
- **Enabled — guards gate the node.** A node is *enabled* when every **required
  gating** binding has a present source and **no** binding whose source is present
  has a false guard. A **false guard disables the node** for that pass — it does
  not fire, and that is how a loop terminates. A `prev` binding is inactive on
  the node's first firing and gates from the second (below), so it never prevents
  the first firing. A binding whose source is absent is **dropped** without
  gating: an optional (`?`) or undeclared input that is unsatisfied simply does
  not constrain the node.
- **Sink enablement.** A sink may appear in any scope (§7) and fires within that
  scope's fixpoint: a sink inside a loop's scope fires on each pass, one
  downstream of the scope fires once (§10.2). A sink fires when its `when` holds,
  no binding has a present source with a false guard, and every input is
  satisfied: a present source, or an absent source the binding marks `?`. An
  absent **optional** (`?`) input is **dropped** — the sink still fires, and the
  input carries no value (the
  `file` sink receives `None`, §4.1) — whereas an absent non-optional input
  disables the sink. That is how `result` and `skipped` in `examples/absence`
  stay mutually exclusive. A sink fires once per enablement like any node and is
  not a binding source (§5). A sink with inputs must have at least one required
  input (§11), so an absent optional is never the node's only reason to fire.
- **Fire on upstream firing.** A node fires when it is enabled and at least one
  of its **dependencies** — a **sibling** node's port (a binding source) or a
  guard reference (below) within the same scope — has
  fired since this node last fired, or it has never fired. Firing is driven by
  **upstream firing, not value equality**: a node re-fires even when a re-fired
  source yields the same value. A firing's outputs are **latest-wins** per port.
  A node that never fires has all outputs absent (it is *skipped*); once it has
  fired it exposes its most recent outputs, and a pass that does not fire it does
  not retract them.
- **An external port is a value, not a source.** A port call never makes a node
  fresh: it cannot by itself trigger or re-fire a node. Freshness comes from node
  firings (a step, the trigger) or the composite boundary `in`; a port only
  supplies a value for an input, whose other inputs must still be present.
- **`prev` bindings are loop-carried.** A binding marked **`prev`** is inactive
  until its node has fired, then an ordinary binding; every unmarked binding
  gates from the start, and declaration order never affects gating. A `prev`
  binding may reference the node itself (a **self-loop**). §10.1 defines the
  marker, its shape, and the loop invariant.
- **Absent drops; false gates.** An absent `?` or undeclared source drops its
  binding and does not gate the node. A *present* source with a false guard
  disables the node (above). An input is required unless marked `?` on the
  binding (`name?: source`) or declared `?` on the unit's port; a `prev` input
  is required only from the node's second firing.
- **Sequential vs parallel is implicit** — there is no `seq` / `par` keyword.
  A binding between two nodes **orders** them; no binding means they **may** run
  concurrently.
- **A guard is a dependency.** The sibling nodes a **guard** (`if`, or a sink's
  `when`) references are dependencies exactly like binding sources: they **order**
  the node — it waits for them — and their firings make it **fresh** (it
  re-fires). So `x: a.x if b.flag` depends on both `a` and `b`. A guard reference
  to the node's **own** name is not a dependency: it reads the node's own latest
  outputs, and a self-loop is expressed by marking a source `prev` (§10.1).
- **Ordering is data-mediated.** To order a step after another without consuming
  its value, thread `terminated_at` (or any value). Every dependency carries a
  value (a guard reads one). A skipped step has no `terminated_at`, so ordering
  edges out of it skip coherently too.
- **Loops and retries.** A loop is a **scope whose graph contains a cycle**
  closed by a `prev` edge — there is no seed primitive and no `while`/`for`; the
  scope still runs to quiescence and yields its `out` once. The `prev` edge is
  inactive on the first pass, so the loop starts; from the second pass it gates
  on its producer and re-fires the node each time that producer fires. A loop is
  **bounded by a guard that disables a node when the budget is reached**:
  attempts while under budget, the exhausted branch once the guard turns false.
  The budget is the loop node's own firing count, **`attempt`** (§5): e.g.
  `review.verdict is rejected and plan.attempt < 3` retries, and the exhausted
  branch reads `plan.attempt >= 3`. (A budget may instead be a loop-carried value
  or an adapter-provided external port, §3.) An **unguarded cycle never quiesces**
  and is aborted by the max-firings bound.
- **Termination.** A scope ends at **quiescence** — no node in it is enabled;
  the Run ends when the root quiesces — or is
  aborted by the max-firings bound. Value equality is irrelevant: a cycle does
  not converge on its own, so termination is the author's concern — a loop ends
  only when a guard disables a node in it, or the bound is hit. The spec fixes no
  number: the bound is the implementation's choice (§11).
- **Factory errors vs node failures.** A **factory error** is the
  implementation's own fault — a runner that cannot execute, a binding that
  violates conformance, a missing `file`, a system fault — and it **aborts the
  Run**. A **node failure** is ordinary data: a step that wants to report failure
  writes it to an `out` port (an author-defined type), and the factory handles it
  with guards and sinks. The engine interprets neither as "success" or "failure."
- **Silence aborts.** A Run that reaches quiescence having fired **no** sink (in
  any scope) **aborts**. On abort — the max-firings bound, a system error, or silence — the
  reserved `abort` source becomes available and the engine runs one final pass
  (the **abort pass**): sinks reading `abort.*` fire. The abort pass fires
  **sinks only** — it never runs a step or enables further work — then the Run
  halts as **aborted**. The one guaranteed field is
  **`abort.reasons: [text]`** — the reasons the Run aborted, as a list of strings
  (the record stays open, §12). That is how a failure sink reports the abort; no
  built-in failure sink is required. The bound is the implementation's choice
  (§11).
- **Outcome.** A `done` Run's outcome is the **set of distinct sinks that fired**
  (in any scope; unordered). A sink may fire more than once — a sink downstream of a
  loop fires on each pass — and every firing is a separate step (its own
  `this.metadata.step.id`), but the outcome names each sink once. An aborted Run's
  status is **aborted**; any sinks that fired (including abort-routed ones) are
  recorded as firings, not as the outcome.

```
unit build:                         # a composite unit
  step a:  use: ...
  step b:  use: ...                 # may run with a
  step c:                           # all-of join: waits for a and b
    use: ...
    in: { x: a.x, y: b.y }
  step d:                           # ordered after a
    use: ...
    in: { gate: a.terminated_at }
```

### 10.1 Forward and `prev` (loop-carried) bindings

A binding is **forward** by default: it gates its node from the first firing.
Marking it **`prev`** makes it **loop-carried** — the feedback edge a cycle needs
in order to start.

```
step plan:
  in:
    prev feedback: review.verdict if review.verdict is rejected and plan.attempt < 3
```

- **Syntax.** `prev` prefixes the binding name: `prev name: [Type =] source
  [if guard]`. It is valid only on a **step input** (`in:`), never on an `out:`
  binding or a sink binding. `prev` is a **reserved word**: a port or binding may
  not be named `prev`, so a leading `prev` is always the marker (§8).
- **Meaning.** A `prev` binding is **inactive until its node has fired**. On the
  node's first firing it contributes no value and does not gate, so it cannot
  prevent that first firing. From the second firing on it is an ordinary binding:
  an absent required source leaves the node not enabled, and a present source
  with a false guard disables the node — that is how a loop ends. Declaration
  order never affects this; it is presentation only.
- **Shape.** A `prev` binding's source must reference **exactly one sibling node**
  (or the node itself), possibly wrapped in arithmetic over constants, e.g.
  `prev n: loop.n + 1`. It may not reference two different siblings or a call.
- **Loop invariant.** The bindings **not** marked `prev` must form a DAG, and
  every `prev` binding must lie on a cycle (a self-loop counts) — the §11 rule,
  restated here. A cycle with no `prev` edge, or a `prev` edge not on a cycle, is
  a spec error. Because a `prev` binding is inactive on the first firing, the
  cycle can be entered; thereafter the producer's firing re-fires the consumer.

### 10.2 Worked examples: a retry loop, and the abort pass

Reusing the §4 `Issue` and `Verdict` types and a per-Run retry port (§6, §10).

**A retry loop with a per-pass effect.** The loop is the *body of* `deliver` — a
scope whose graph contains a `prev` cycle. `plan` fires once, then re-fires each
pass `review` fires; the guard on the feedback binding disables `plan` once the
budget is spent (or the verdict is approved), which ends the loop. `deliver`
itself fires once and yields one `verdict`. The `progress` sink sits *inside* the
scope, next to the loop, so it fires on every pass; `succeeded` / `failed` sit
*downstream of* the scope, so each fires once (when its `when` holds).

```
loop deliver:                       # the loop's scope: fires once, yields one verdict
  in:  { ticket: Issue }
  out: { verdict: Verdict = review.verdict }

  step plan:                        # feedback: review -> plan
    runner: platform
    in:
      prompt:        "plan the issue"
      ticket:        in.ticket
      prev feedback: review.verdict if review.verdict is failed and plan.attempt < 3
    out: { plan: text }

  step review:
    runner: platform
    in:  { prompt: "review the plan", plan: plan.plan }
    out: { verdict: Verdict }

  sink progress:                    # inside the scope: fires once per pass
    kind: webhook
    in: { attempt: plan.attempt,
          plan:    plan.plan }

trigger manual_ticket:
  kind: manual
  out: { ticket: Issue }

step run:
  use: deliver
  in:  { ticket: manual_ticket.ticket }

sink succeeded:                     # downstream of the scope: fires once
  kind: file
  path: "out/succeeded.json"
  in:  { verdict: run.verdict }
  when: run.verdict is succeeded

sink failed:
  kind: file
  path: "out/failed.json"
  in:  { verdict: run.verdict }
  when: run.verdict is failed
```

If `progress` fires on three passes and `succeeded` once, the Run's outcome is
the set `{progress, succeeded}` — each sink named once — while the event log has
three `progress` firings, each with its own `step.id`.

**The abort pass.** Suppose instead every sink above stays disabled: no sink
fires, so the Run is **silent** and aborts. `abort.*` then becomes available and
the `aborted` sink fires — that final evaluation is the *abort pass*. The same
sink fires when the max-firings bound or a system error aborts the Run. Such a
Run's status is `aborted`; the sink firing is recorded, not counted as the
outcome.

```
sink aborted:
  kind: file
  path: "out/aborted.json"
  in:  { reasons: abort.reasons }     # present only while the Run is aborting
```

## 11. Determinism & invariants

**Determinism.** The interface (ports + bindings) is fully declared and every
value crossing a binding is recorded, keyed by a hash of its content
(**content-addressed**): identical values are stored once, and a Run's trace can
be replayed and checked. Conformance is a *runtime* check. This is reproducible
*interface and trace*, not reproducible *output*: execution is not hermetic
(agents are nondeterministic).

**Invariants.**

- A factory declares ≥ 1 Trigger and ≥ 1 Sink.
- Every block is closed (§6.3): each field is spec-defined or declared by its
  kind, and each binding names a declared port (or a field of the runner's `in:`
  interface). An undeclared name is a spec error.
- A leaf unit provides its runner's `in:` / `out:` interface (§6): every
  required interface field is bound.
- Required ports must be bound by a step.
- A node with bindings has at least one that is **not optional** (`?`): an
  all-optional binding set is a spec error, because an absent optional would be
  indistinguishable from a producer that has not fired yet — the node could not
  tell whether it is *waiting* or *firing* (§10).
- Every `TypeRef` resolves to a built-in or a declared type.
- A factory is rooted at exactly one module; every declaration is reachable
  through explicit imports.
- Every referenced declaration resolves (type, unit, runner, trigger, sink,
  external port) across modules.
- Every binding source and guard path resolves statically: the enclosing scope
  names a visible node (`in` / `out` / `abort` / `this`, a root trigger, or a
  sibling step),
  the first segment names a declared port on that node (`terminated_at` and
  `attempt` are implicit on every trigger/step and take no subfields), and each
  further segment resolves against the port's declared type (record field, numeric
  list index, or choice tag). `abort` guarantees `reasons: [text]` and otherwise
  accepts any segments (shape open); `this` is
  the closed record `{ metadata: { run: { id: text }, node: { id: text }, step: { id: text } } }`.
  An external-port call binds exactly
  its declared params. Anything else is a spec error at load time; the only
  runtime absence is a declared optional (`?`) port left unproduced.
- Gating is explicit: within a scope, a binding's dependencies
  are the sibling nodes its **source** and its **guard** reference (a guard's
  reference to the node's own name is not a dependency); the bindings **not**
  marked `prev` form a directed acyclic graph, and every `prev` binding lies on a
  cycle (a self-loop counts) — a `prev` binding being inactive until its node has
  fired. A cycle with no `prev` edge, or a `prev` edge not on a cycle, is a spec
  error.
- A binding's optional `name: Type = source` annotation is an upper bound: the
  source's type must conform to it and (for a referenced step) it must conform to
  the unit's declared port type.
- A required `out:` (no `?`) bound to a possibly-absent source — a port of a
  step that may not fire, or navigation through a declared-optional field —
  is a spec error (make it `?` or add a covering branch). Bare `out:` ports
  (no `=`) are exposed with open subsetting semantics — what supplies them is
  still open (§12) — and are not coverage-checked.

## 12. Open (deferred, additive)

- Further operators: arithmetic (`+ - * / %`) is specified; bitwise, string
  concatenation and other operators are not.
- Run status, cancellation and resume.
- Provider registry API (how adapters are registered and resolved, and how a
  kind declares the config fields it accepts — the §6.3 closure).
- Value representation and content-addressing.
- Trigger seed syntax (how a trigger supplies its `out` ports at admission).
- Remaining `abort` fields: `reasons: [text]` is defined; the rest of the shape
  is open.
- `unit` ← `unit` inheritance detail (whether `in` / `out` ports merge by name).
- Bare `out:` subsetting: what supplies a port declared with no `=`.
- Referencing a sink's `terminated_at` to order work after an effect: sinks are
  effect nodes that fire, but are not yet binding sources.
- The abort pass across scopes: whether an abort fires abort-routed sinks in
  every scope, or only in the scope where it occurred.
- Consolidating `use:`-recursion with `prev` iteration, now that every unit is a
  scope that runs to quiescence — whether recursion is still needed.

## Appendix A — Worked example (multi-file)

A factory split across modules. Leaf work is **inlined at the step**; only reused
or imported pieces — types, runners, external ports and the composite unit — are
named.

```
# ── types.fct ─────────────────────────────────────────────────
type Repository = {
  name: text
  url:  text
}

type Commit = {
  sha:        text
  repository: Repository
}

type Plan = {
  text: text
}

type Severity = enum { low, medium, high }

type Finding = {
  severity:    Severity
  location:    text
  description: text
}

type ReviewVerdict = choice {
  approved { summary: text }
  rejected { findings: [Finding], feedback: text }
}

type GateResult = {
  name:    text
  passed:  bool
  output?: text
}

type QualityReport = {
  lint:      GateResult
  typecheck: GateResult
  unittests: GateResult
  uat:       GateResult
}

type Failure = choice {
  plan_exhausted  { findings: [Finding], feedback: text }
  build_exhausted { report: QualityReport }
}

type PullRequest = {
  number: int
  url:    text
}
```

```
# ── input.fct ─────────────────────────────────────────────────
type JiraTicket = {
  key:    text          # e.g. "ACME-123"
  title:  text
  body:   text
  labels: [text]?
  url:    text?
}
```

```
# ── runners.fct ───────────────────────────────────────────────
type RepoMount = {
  source:  text
  target:  text
  include: [text]?
  exclude: [text]?
  mode:    enum { ro, rw }?
}

type DockerEnv = {
  image:   text
  cpu:     int?
  network: enum { none, host, bridge }?
  workdir: text?
  region:  text?
  mounts:  [text]?
  repos:   [RepoMount]?
}

runner platform:                  # the platform's root runner
  kind:   docker
  config: DockerEnv = {
    image:   "factory-vm:base"
    cpu:     2
    network: none
    workdir: /workspace
    region:  eu-west-1
  }

runner builder:                   # build/test: toolchains + dependency cache
  extends: platform
  config:  DockerEnv = {
    image:  "factory-vm:builder"
    mounts: ["/cache/deps"]
    repos: [
      {
        source:  "./repo"
        target:  "/workspace/repo"
        include: ["src/**", "Makefile", "pyproject.toml"]
        exclude: ["**/__pycache__/**"]
        mode:    ro
      }
    ]
  }
```

```
# ── factory.fct (root) ────────────────────────────────────────
import "types"   as t
import "input"   as i
import "runners" as r

loop deliver:
  in:  { ticket: i::JiraTicket }
  out:
    commit:        t::Commit       = implement.commit
    pr:            t::PullRequest? = open_pr.pr
    plan_failure:  t::Failure?     = plan_failure.failure
    build_failure: t::Failure?     = build_failure.failure

  # ---- plan, and have the plan reviewed; re-plan with feedback, up to 3 times
  step plan:
    runner: r::platform
    in:
      prompt:   file "prompts/planner.md"
      ticket:   in.ticket
      feedback?: review.verdict if review.verdict is rejected and plan.attempt < 3
    out: { plan: t::Plan }

  step review:
    runner: r::platform
    in:  { prompt: file "prompts/adversary.md", plan: plan.plan }
    out: { verdict: t::ReviewVerdict }

  # ---- implement the approved plan
  step implement:
    runner: r::builder
    in:
      prompt:   file "prompts/implementer.md"
      ticket:   in.ticket
      plan:     plan.plan
      feedback?: gate.report if not gate.passed and implement.attempt < 3
    out: { commit: t::Commit }

  # ---- quality gates: all four run in parallel on the produced commit
  step lint:
    runner: r::builder
    in:  { command: "make lint", commit: implement.commit }
    out: { result: t::GateResult }

  step typecheck:
    runner: r::builder
    in:  { command: "make typecheck", commit: implement.commit }
    out: { result: t::GateResult }

  step unittests:
    runner: r::builder
    in:  { command: "make test", commit: implement.commit }
    out: { result: t::GateResult }

  step uat:
    runner: r::builder
    in:  { command: "make uat", commit: implement.commit }
    out: { result: t::GateResult }

  # ---- join the gates (all-of)
  step gate:
    runner: r::platform
    in:
      command:   "factory-gate"
      lint:      lint.result
      typecheck: typecheck.result
      unittests: unittests.result
      uat:       uat.result
    out:
      passed: bool
      report: t::QualityReport

  # ---- open the PR only if every gate passed
  step open_pr:
    runner: r::platform
    in:
      command: "gh pr create --fill"
      commit:  implement.commit if gate.passed
    out: { pr: t::PullRequest }

  # ---- failure exits: fire when a retry budget is exhausted
  step plan_failure:
    runner: r::platform
    in:
      command: "factory-fail plan"
      ticket:  in.ticket
      verdict: review.verdict if review.verdict is rejected and plan.attempt >= 3
    out: { failure: t::Failure }

  step build_failure:
    runner: r::platform
    in:
      command: "factory-fail build"
      ticket:  in.ticket
      report:  gate.report if not gate.passed and implement.attempt >= 3
    out: { failure: t::Failure }

trigger manual_ticket:                # a manual trigger (fires with provided input)
  kind: manual
  out: { ticket: i::JiraTicket }

step run:                             # root invocation: binds the workflow's inputs
  use: deliver
  in:  { ticket: manual_ticket.ticket }

sink write_result:
  kind: file
  path: "out/result.json"
  in:  { commit: run.commit, pr: run.pr }

sink plan_needs_attention:
  kind: file
  path: "out/plan_needs_attention.json"
  in:  { failure: run.plan_failure }

sink build_needs_attention:
  kind: file
  path: "out/build_needs_attention.json"
  in:  { failure: run.build_failure }

sink aborted:
  kind: file
  path: "out/aborted.json"
  in:  { reasons: abort.reasons }
```

---

## Changelog

- **v1.21** — **`loop` is a declaration keyword.** `loop name:` declares a
  composite unit that **must** contain a `prev` cycle — a load-time error
  without one — while `unit name:` may or may not loop. Otherwise identical:
  both are scopes (§7, §10). A loop that is only implicit (a `unit` whose graph
  happens to contain a `prev`) is still a loop; the keyword just states it.
- **v1.20** — **a closed language and typed runner interfaces.** A runner
  declares its implementation's interface — `in:` / `out:` (types) — and its
  static `config` (a typed value); a leaf unit conforms to that interface, and a
  **generic** runner (no interface) lets each leaf declare its own ports. Every
  block is **closed**: its fields are the spec-defined ones plus those its kind
  declares, and an unknown or misspelled field is a **load-time error**, never
  silently ignored (§6.3). `prompt:` and `auth:` are no longer fields — an
  agent's instruction is `prompt: text` in the runner's `in:` interface and its
  credential is `token: secret`; a leaf's `command` is its kind's field (or an
  `in:` input).
- **v1.19** — execution is defined over **scopes**. A scope (the root, or a
  composite body) runs to quiescence as one firing, and only its boundary ports
  cross; a unit is a scope, and it need not loop — a loop is a scope whose graph
  contains a `prev` cycle. Sinks may appear in any scope, so a per-pass
  effect sits inside the loop's scope and a once-only effect sits downstream of
  it. Fixed §10.2, whose sinks referenced nodes in another scope.
- **v1.18** — every receiver (port, leaf, sink) gets a secret as a secret, so it
  knows; the receiver unwraps it to use it and reports the marker.
- **v1.17** — a boolean context requires a `bool`; no truthiness of numbers or
  strings; `bool` and `int` are distinct (`true != 1`).
- **v1.16** — Python 3 is the reference for value semantics where the spec is
  silent; mixed `int`/`float` comparison; `%` takes the sign of the divisor.
- **v1.15** — no implicit `int`→`float` conversion.
- **v1.14** — `float` is finite; overflow and underflow abort.
- **v1.13** — `int` is a signed 64-bit two's-complement integer; out of range
  aborts.
- **v1.12** — restated `int`'s range.
- **v1.11** — added the `secret` primitive (redacted at every observation).
- **v1.10** — defined `float`; named the value `None`.
- **v1.9** — defined `text` (UTF-8) and `int`.
- **v1.8** — defined `None`; distinguished it from absence.
- **v1.7** — explicit loop-carried `prev` edges; optional local binding types.
