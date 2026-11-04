package app;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Function;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The Markdown renderer: it escapes the whole body first and only then adds the supported markup (headings, emphasis,
 * lists, links, images, block quotes, inline code, fenced code blocks), so stored HTML is never served raw; link and
 * image targets must be http(s), mailto or relative.
 */
public final class Markdown {

    private static final Pattern SAFE_URL =
            Pattern.compile("^(https?://|mailto:|/|#|\\./|\\.\\./|[^:/?#]+(?:[/?#]|$))", Pattern.CASE_INSENSITIVE);
    private static final Pattern IMAGE = Pattern.compile("!\\[([^\\]]*)\\]\\(([^)\\s]+)\\)");
    private static final Pattern LINK = Pattern.compile("\\[([^\\]]+)\\]\\(([^)\\s]+)\\)");
    private static final Pattern STRONG = Pattern.compile("\\*\\*(.+?)\\*\\*|__(.+?)__");
    private static final Pattern EM = Pattern.compile("\\*(.+?)\\*|_(.+?)_");
    private static final Pattern STASH = Pattern.compile("\u0000(\\d+)\u0000");
    private static final Pattern HEADING = Pattern.compile("^(#{1,6})\\s+(.*?)\\s*#*\\s*$");
    private static final Pattern LIST = Pattern.compile("^([-*+]|\\d+\\.)\\s+");
    private static final Pattern OL = Pattern.compile("^\\d+\\.\\s+");
    private static final Pattern UL = Pattern.compile("^[-*+]\\s+");

    private Markdown() {}

    static String escape(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")
                .replace("'", "&#x27;");
    }

    static String unescape(String s) {
        return s.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&#x27;", "'")
                .replace("&amp;", "&");
    }

    private static String replace(Pattern pattern, String text, Function<Matcher, String> fn) {
        Matcher m = pattern.matcher(text);
        StringBuilder out = new StringBuilder();
        while (m.find()) {
            m.appendReplacement(out, Matcher.quoteReplacement(fn.apply(m)));
        }
        m.appendTail(out);
        return out.toString();
    }

    private static String url(String raw) {
        String target = unescape(raw);
        return SAFE_URL.matcher(target).find() ? escape(target) : null;
    }

    private static String either(Matcher m) {
        return m.group(1) != null ? m.group(1) : m.group(2);
    }

    static String inline(String text) {
        List<String> parts = new ArrayList<>(List.of(text.split("`", -1)));
        if (parts.size() % 2 == 0) {
            int n = parts.size();
            String merged = parts.get(n - 2) + "`" + parts.get(n - 1);
            parts.remove(n - 1);
            parts.set(n - 2, merged);
        }
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < parts.size(); i++) {
            String part = parts.get(i);
            if (i % 2 == 1) {
                out.append("<code>").append(part).append("</code>");
                continue;
            }
            List<String> stash = new ArrayList<>();
            Function<String, String> keep = markup -> {
                stash.add(markup);
                return "\u0000" + (stash.size() - 1) + "\u0000";
            };
            part = replace(IMAGE, part, m -> {
                String u = url(m.group(2));
                return u == null ? m.group(0) : keep.apply("<img src=\"" + u + "\" alt=\"" + m.group(1) + "\">");
            });
            part = replace(LINK, part, m -> {
                String u = url(m.group(2));
                return u == null ? m.group(0) : keep.apply("<a href=\"" + u + "\">" + m.group(1) + "</a>");
            });
            part = replace(STRONG, part, m -> "<strong>" + either(m) + "</strong>");
            part = replace(EM, part, m -> "<em>" + either(m) + "</em>");
            part = replace(STASH, part, m -> stash.get(Integer.parseInt(m.group(1))));
            out.append(part);
        }
        return out.toString();
    }

    public static String render(String markdown) {
        String[] lines = escape(markdown.replace("\u0000", "")).replace("\r\n", "\n").split("\n", -1);
        List<String> out = new ArrayList<>();
        List<String> para = new ArrayList<>();
        Runnable flush = () -> {
            if (!para.isEmpty()) {
                out.add("<p>" + inline(String.join(" ", para)) + "</p>");
                para.clear();
            }
        };
        int i = 0;
        while (i < lines.length) {
            String stripped = lines[i].strip();
            Matcher heading = HEADING.matcher(stripped);
            if (stripped.startsWith("```")) {
                flush.run();
                List<String> code = new ArrayList<>();
                i++;
                while (i < lines.length && !lines[i].strip().startsWith("```")) {
                    code.add(lines[i++]);
                }
                out.add("<pre><code>" + String.join("\n", code) + "</code></pre>");
                i++;
                continue;
            } else if (heading.matches()) {
                flush.run();
                int level = heading.group(1).length();
                out.add("<h" + level + ">" + inline(heading.group(2)) + "</h" + level + ">");
            } else if (stripped.startsWith("&gt;")) {
                flush.run();
                List<String> quote = new ArrayList<>();
                while (i < lines.length && lines[i].strip().startsWith("&gt;")) {
                    quote.add(lines[i++].strip().substring(4).strip());
                }
                out.add("<blockquote><p>" + inline(String.join(" ", quote)) + "</p></blockquote>");
                continue;
            } else if (LIST.matcher(stripped).find()) {
                flush.run();
                boolean ordered = OL.matcher(stripped).find();
                Pattern pattern = ordered ? OL : UL;
                StringBuilder items = new StringBuilder();
                while (i < lines.length && pattern.matcher(lines[i].strip()).find()) {
                    items.append("<li>").append(inline(pattern.matcher(lines[i++].strip()).replaceFirst("")))
                            .append("</li>");
                }
                String tag = ordered ? "ol" : "ul";
                out.add("<" + tag + ">" + items + "</" + tag + ">");
                continue;
            } else if (stripped.isEmpty()) {
                flush.run();
            } else {
                para.add(stripped);
            }
            i++;
        }
        flush.run();
        return String.join("\n", out);
    }
}
