export type Row = Record<string, any>
export type View = 'batches' | 'review' | 'quota' | 'settings'
export interface BatchBody { title: string; items: unknown[] }
export interface PendingSubmission { key: string; body: unknown }
