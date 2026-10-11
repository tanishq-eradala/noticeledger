# NoticeLedger design

The primary user is an operations reviewer deciding whether a notice can be recorded. The defining interaction is a field-to-source connection: selecting a value highlights the exact source line, while corrections preserve the original extraction and a written reason.

Palette: deep sea #163c53 for navigation, ink #152a38 for text, cloud #f2f6f8 for the workspace, white #ffffff for documents, teal #146a63 for accepted data, amber #8a5100 for exceptions. Body uses the platform sans-serif; notice headings use Georgia to distinguish the source document from application controls. Amounts use tabular figures rather than a separate monospace face.

Desktop layout: narrow navigation, searchable review queue, editable field worksheet, source notice. The queue and worksheet are the main work surface; aggregate counts are small navigation aids. Ledger and processing history are separate views. Mobile stacks the worksheet and evidence, with a real horizontal scroll container for the ledger.

Buttons name their action: Import notices, Save corrections, Approve notice, Reject notice. Approval is disabled when unresolved checks remain, but the server enforces the same rule. The original source is immutable. Every page explains its empty state and preserves actionable errors. Keyboard focus is visible, dialogs restore focus, and loading uses a status message.
