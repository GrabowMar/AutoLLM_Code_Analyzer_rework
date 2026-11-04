package main

// The Markdown renderer escapes the whole body first and only then adds the supported markup (headings, emphasis,
// lists, links, images, block quotes, inline code, fenced code blocks), so stored HTML is never served raw; link and
// image targets must be http(s), mailto or relative.

import (
	"fmt"
	"html"
	"regexp"
	"strconv"
	"strings"
)

var (
	safeURL   = regexp.MustCompile(`(?i)^(https?://|mailto:|/|#|\./|\.\./|[^:/?#]+(?:[/?#]|$))`)
	imageRe   = regexp.MustCompile(`!\[([^\]]*)\]\(([^)\s]+)\)`)
	linkRe    = regexp.MustCompile(`\[([^\]]+)\]\(([^)\s]+)\)`)
	strongRe  = regexp.MustCompile(`\*\*(.+?)\*\*|__(.+?)__`)
	emRe      = regexp.MustCompile(`\*(.+?)\*|_(.+?)_`)
	stashRe   = regexp.MustCompile("\x00(\\d+)\x00")
	headingRe = regexp.MustCompile(`^(#{1,6})\s+(.*?)\s*#*\s*$`)
	listRe    = regexp.MustCompile(`^([-*+]|\d+\.)\s+`)
	olRe      = regexp.MustCompile(`^\d+\.\s+`)
	ulRe      = regexp.MustCompile(`^[-*+]\s+`)
	escaper   = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#x27;")
)

// replaceFunc is ReplaceAllStringFunc with the submatches.
func replaceFunc(re *regexp.Regexp, s string, fn func(m []string) string) string {
	var b strings.Builder
	last := 0
	for _, idx := range re.FindAllStringSubmatchIndex(s, -1) {
		b.WriteString(s[last:idx[0]])
		m := make([]string, len(idx)/2)
		for i := range m {
			if idx[2*i] >= 0 {
				m[i] = s[idx[2*i]:idx[2*i+1]]
			}
		}
		b.WriteString(fn(m))
		last = idx[1]
	}
	b.WriteString(s[last:])
	return b.String()
}

func cleanURL(raw string) (string, bool) {
	target := html.UnescapeString(raw)
	if !safeURL.MatchString(target) {
		return "", false
	}
	return escaper.Replace(target), true
}

func firstOf(a, b string) string {
	if a != "" {
		return a
	}
	return b
}

func inline(text string) string {
	parts := strings.Split(text, "`")
	if len(parts)%2 == 0 {
		n := len(parts)
		parts = append(parts[:n-2], parts[n-2]+"`"+parts[n-1])
	}
	var out strings.Builder
	for i, part := range parts {
		if i%2 == 1 {
			out.WriteString("<code>" + part + "</code>")
			continue
		}
		var stash []string
		keep := func(markup string) string {
			stash = append(stash, markup)
			return fmt.Sprintf("\x00%d\x00", len(stash)-1)
		}
		part = replaceFunc(imageRe, part, func(m []string) string {
			if u, ok := cleanURL(m[2]); ok {
				return keep(`<img src="` + u + `" alt="` + m[1] + `">`)
			}
			return m[0]
		})
		part = replaceFunc(linkRe, part, func(m []string) string {
			if u, ok := cleanURL(m[2]); ok {
				return keep(`<a href="` + u + `">` + m[1] + `</a>`)
			}
			return m[0]
		})
		part = replaceFunc(strongRe, part, func(m []string) string { return "<strong>" + firstOf(m[1], m[2]) + "</strong>" })
		part = replaceFunc(emRe, part, func(m []string) string { return "<em>" + firstOf(m[1], m[2]) + "</em>" })
		part = replaceFunc(stashRe, part, func(m []string) string {
			n, _ := strconv.Atoi(m[1])
			return stash[n]
		})
		out.WriteString(part)
	}
	return out.String()
}

func render(markdown string) string {
	escaped := escaper.Replace(strings.ReplaceAll(markdown, "\x00", ""))
	lines := strings.Split(strings.ReplaceAll(escaped, "\r\n", "\n"), "\n")
	var out, para []string
	flush := func() {
		if len(para) > 0 {
			out = append(out, "<p>"+inline(strings.Join(para, " "))+"</p>")
			para = nil
		}
	}
	for i := 0; i < len(lines); {
		stripped := strings.TrimSpace(lines[i])
		switch {
		case strings.HasPrefix(stripped, "```"):
			flush()
			var code []string
			for i++; i < len(lines) && !strings.HasPrefix(strings.TrimSpace(lines[i]), "```"); i++ {
				code = append(code, lines[i])
			}
			out = append(out, "<pre><code>"+strings.Join(code, "\n")+"</code></pre>")
			i++
			continue
		case headingRe.MatchString(stripped):
			flush()
			m := headingRe.FindStringSubmatch(stripped)
			level := len(m[1])
			out = append(out, fmt.Sprintf("<h%d>%s</h%d>", level, inline(m[2]), level))
		case strings.HasPrefix(stripped, "&gt;"):
			flush()
			var quote []string
			for ; i < len(lines) && strings.HasPrefix(strings.TrimSpace(lines[i]), "&gt;"); i++ {
				quote = append(quote, strings.TrimSpace(strings.TrimSpace(lines[i])[4:]))
			}
			out = append(out, "<blockquote><p>"+inline(strings.Join(quote, " "))+"</p></blockquote>")
			continue
		case listRe.MatchString(stripped):
			flush()
			pattern, tag := ulRe, "ul"
			if olRe.MatchString(stripped) {
				pattern, tag = olRe, "ol"
			}
			var items strings.Builder
			for ; i < len(lines) && pattern.MatchString(strings.TrimSpace(lines[i])); i++ {
				items.WriteString("<li>" + inline(pattern.ReplaceAllString(strings.TrimSpace(lines[i]), "")) + "</li>")
			}
			out = append(out, "<"+tag+">"+items.String()+"</"+tag+">")
			continue
		case stripped == "":
			flush()
		default:
			para = append(para, stripped)
		}
		i++
	}
	flush()
	return strings.Join(out, "\n")
}
