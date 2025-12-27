import React, { useRef } from 'react';
import { Milkdown, useEditor } from '@milkdown/react';
import { Editor, rootCtx, defaultValueCtx, prosePluginsCtx } from '@milkdown/kit/core';
import { commonmark } from '@milkdown/kit/preset/commonmark';
import { gfm } from '@milkdown/kit/preset/gfm';
import { nord } from '@milkdown/theme-nord';
import { listener, listenerCtx } from '@milkdown/plugin-listener';
import { Plugin, PluginKey } from '@milkdown/kit/prose/state';
import { Decoration, DecorationSet } from '@milkdown/kit/prose/view';

interface MilkdownEditorProps {
    content: string;
    onChange: (markdown: string) => void;
}

// Custom ProseMirror Plugin for Variable Highlighting
const variableHighlightPlugin = new Plugin({
    key: new PluginKey('variable-highlight'),
    props: {
        decorations(state) {
            const decorations: Decoration[] = [];
            const doc = state.doc;

            doc.descendants((node, pos) => {
                if (!node.isText) return;

                const text = node.text || '';
                const regex = /\{([\w.-]+)\}/g;
                let match;

                while ((match = regex.exec(text)) !== null) {
                    const start = pos + match.index;
                    const end = start + match[0].length;

                    decorations.push(
                        Decoration.inline(start, end, {
                            class: 'inline-flex items-center px-1.5 py-0.5 mx-0.5 rounded-full bg-amber-100/80 text-amber-700/90 font-mono text-xs border border-amber-200/60 font-medium select-none shadow-sm translate-y-[-1px]',
                            title: '变量'
                        })
                    );
                }
            });

            return DecorationSet.create(state.doc, decorations);
        }
    }
});

const MilkdownEditorContent: React.FC<MilkdownEditorProps> = ({ content, onChange }) => {
    // Keep a ref to avoid re-creating editor on content change (except initial)
    const contentRef = useRef(content);
    // Only update ref if the new content is significantly different to avoid loops? 
    // Actually defaultValueCtx handles initial. For updates we might need a different approach if we want 2-way sync from outside, 
    // but typically the editor is the source of truth.
    // We'll stick to 'uncontrolled' inner state with initial value for now, or handle updates carefully.

    useEditor((root) => {
        return Editor.make()
            .config((ctx) => {
                ctx.set(rootCtx, root);
                ctx.set(defaultValueCtx, contentRef.current);
                ctx.get(listenerCtx).markdownUpdated((_ctx, markdown, prevMarkdown) => {
                    if (markdown !== prevMarkdown) {
                        onChange(markdown);
                    }
                });
            })
            .config((ctx) => {
                // Register custom plugin
                ctx.update(prosePluginsCtx, (plugins) => [
                    ...(plugins as any[]),
                    variableHighlightPlugin
                ]);
            })
            .use(commonmark)
            .use(gfm)
            .use(nord as any)
            .use(listener);
    }, []);

    return <Milkdown />;
};

export default MilkdownEditorContent;
