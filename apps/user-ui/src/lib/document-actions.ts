// Document actions that can be asked for from outside the preview (the tree
// and list context menu): the document is opened and its preview runs the
// action once - the export forms and the redaction dialog live there.
export type DocumentAction = "download" | "export" | "xdomea" | "xjustiz" | "redact";
