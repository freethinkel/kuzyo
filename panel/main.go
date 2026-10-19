// panel shows the Docker containers of kuzyo's host, apps and pets on one
// page, with their load, logs and start/stop/restart. It talks to each
// Docker Engine API over its unix socket; the apps and pets sockets reach the
// host through Incus proxy devices (site.yml).
package main

import (
	"bufio"
	"context"
	"crypto/subtle"
	_ "embed"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"html/template"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"slices"
	"strings"
	"sync"
	"time"
)

const (
	interval = 15 * time.Second
	histLen  = 40 // 10 minutes of CPU history at one sample per interval
)

//go:embed page.html
var pageHTML string

var tmpl = template.Must(template.New("").Funcs(template.FuncMap{"spark": spark, "size": size}).Parse(pageHTML))

type host struct {
	name string
	http *http.Client
}

func newHost(name, sock string) host {
	return host{name, &http.Client{Transport: &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return (&net.Dialer{}).DialContext(ctx, "unix", sock)
		},
	}}}
}

// call makes a Docker Engine API request; the caller closes the body.
func (h host) call(ctx context.Context, method, path string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, method, "http://docker"+path, nil)
	if err != nil {
		return nil, err
	}
	resp, err := h.http.Do(req)
	if err != nil {
		return nil, err
	}
	// 304 is a start of a running container or a stop of a stopped one.
	if resp.StatusCode >= 400 {
		defer resp.Body.Close()
		var e struct{ Message string }
		json.NewDecoder(resp.Body).Decode(&e)
		return nil, fmt.Errorf("%s: %s", h.name, e.Message)
	}
	return resp, nil
}

func (h host) get(ctx context.Context, path string, out any) error {
	resp, err := h.call(ctx, "GET", path)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return json.NewDecoder(resp.Body).Decode(out)
}

type container struct {
	Host, ID, Name, Project, Image, State, Status string
	CPU                                           float64 // percent of one core, like docker stats
	Mem                                           uint64
	Hist                                          []float64
}

func (h host) containers(ctx context.Context) ([]container, error) {
	var list []struct {
		ID                   string `json:"Id"`
		Names                []string
		Image, State, Status string
		Labels               map[string]string
	}
	if err := h.get(ctx, "/containers/json?all=1", &list); err != nil {
		return nil, err
	}
	cs := make([]container, len(list))
	for i, c := range list {
		cs[i] = container{Host: h.name, ID: c.ID, Name: strings.TrimPrefix(c.Names[0], "/"),
			Project: c.Labels["com.docker.compose.project"], Image: c.Image, State: c.State, Status: c.Status}
	}
	slices.SortFunc(cs, func(a, b container) int {
		return strings.Compare(a.Project+"/"+a.Name, b.Project+"/"+b.Name)
	})
	return cs, nil
}

type stats struct {
	CPU struct {
		Usage struct {
			Total uint64 `json:"total_usage"`
		} `json:"cpu_usage"`
		System uint64 `json:"system_cpu_usage"`
		Online uint64 `json:"online_cpus"`
	} `json:"cpu_stats"`
	Mem struct {
		Usage uint64            `json:"usage"`
		Stats map[string]uint64 `json:"stats"`
	} `json:"memory_stats"`
}

// memUsed leaves out the page cache the kernel can drop, as docker stats does.
func (s stats) memUsed() uint64 {
	cache := s.Mem.Stats["inactive_file"] // cgroup v2
	if v, ok := s.Mem.Stats["total_inactive_file"]; ok {
		cache = v // cgroup v1
	}
	return s.Mem.Usage - min(cache, s.Mem.Usage)
}

type sample struct{ total, system uint64 }

// cpuPercent is the container's share of the host's CPU time between two
// samples, in percent of one core.
func cpuPercent(prev, cur sample, online uint64) float64 {
	if cur.system <= prev.system || cur.total < prev.total {
		return 0
	}
	return float64(cur.total-prev.total) / float64(cur.system-prev.system) * float64(online) * 100
}

type hostView struct {
	Name, Err  string
	CPU        float64
	Mem        uint64
	Containers []container
}

type panel struct {
	hosts []host

	pollMu sync.Mutex // one poll at a time, so CPU samples follow each other
	prev   map[string]sample
	hist   map[string][]float64

	mu   sync.Mutex
	view []hostView
}

func (p *panel) host(name string) (host, bool) {
	i := slices.IndexFunc(p.hosts, func(h host) bool { return h.name == name })
	if i < 0 {
		return host{}, false
	}
	return p.hosts[i], true
}

func (p *panel) poll() {
	p.pollMu.Lock()
	defer p.pollMu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	view := make([]hostView, len(p.hosts))
	seen := map[string]bool{}
	for i, h := range p.hosts {
		v := hostView{Name: h.name}
		cs, err := h.containers(ctx)
		if err != nil {
			v.Err = err.Error()
		}
		st := make([]*stats, len(cs))
		var wg sync.WaitGroup
		for j, c := range cs {
			if c.State != "running" {
				continue
			}
			wg.Go(func() {
				var s stats
				if h.get(ctx, "/containers/"+c.ID+"/stats?stream=false&one-shot=true", &s) == nil {
					st[j] = &s
				}
			})
		}
		wg.Wait()
		for j := range cs {
			c := &cs[j]
			key := h.name + "/" + c.ID
			seen[key] = true
			s := st[j]
			if s == nil {
				delete(p.prev, key)
				continue
			}
			cur := sample{s.CPU.Usage.Total, s.CPU.System}
			if old, ok := p.prev[key]; ok {
				c.CPU = cpuPercent(old, cur, s.CPU.Online)
			}
			p.prev[key] = cur
			c.Mem = s.memUsed()
			hist := append(p.hist[key], c.CPU)
			p.hist[key] = hist[max(0, len(hist)-histLen):]
			c.Hist = slices.Clone(p.hist[key])
			v.CPU += c.CPU
			v.Mem += c.Mem
		}
		v.Containers = cs
		view[i] = v
	}
	for key := range p.hist {
		if !seen[key] {
			delete(p.hist, key)
		}
	}
	p.mu.Lock()
	p.view = view
	p.mu.Unlock()
}

func (p *panel) index(w http.ResponseWriter, r *http.Request) {
	p.mu.Lock()
	hosts := p.view
	p.mu.Unlock()
	load := "?"
	if b, err := os.ReadFile("/proc/loadavg"); err == nil {
		load = strings.Join(strings.Fields(string(b))[:3], " ")
	}
	total, avail := meminfo()
	tmpl.ExecuteTemplate(w, "index", map[string]any{
		"Load":     load,
		"MemUsed":  total - avail,
		"MemTotal": total,
		"Hosts":    hosts,
	})
}

// meminfo reads the machine's memory; a container shares /proc/meminfo with
// the host.
func meminfo() (total, avail uint64) {
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return 0, 0
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		var kb uint64
		if n, _ := fmt.Sscanf(sc.Text(), "MemTotal: %d kB", &kb); n == 1 {
			total = kb << 10
		}
		if n, _ := fmt.Sscanf(sc.Text(), "MemAvailable: %d kB", &kb); n == 1 {
			avail = kb << 10
		}
	}
	return total, avail
}

func (p *panel) action(w http.ResponseWriter, r *http.Request) {
	h, ok := p.host(r.FormValue("host"))
	op := r.FormValue("op")
	if !ok || !slices.Contains([]string{"start", "stop", "restart"}, op) {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), time.Minute)
	defer cancel()
	resp, err := h.call(ctx, "POST", "/containers/"+url.PathEscape(r.FormValue("id"))+"/"+op)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	resp.Body.Close()
	p.poll()
	http.Redirect(w, r, "/", http.StatusSeeOther)
}

func (p *panel) logs(w http.ResponseWriter, r *http.Request) {
	tmpl.ExecuteTemplate(w, "logs", map[string]string{
		"Host": r.FormValue("host"), "ID": r.FormValue("id"), "Name": r.FormValue("name"),
	})
}

// logStream follows a container's log as server-sent events, one per line.
func (p *panel) logStream(w http.ResponseWriter, r *http.Request) {
	h, ok := p.host(r.FormValue("host"))
	if !ok {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	id := "/containers/" + url.PathEscape(r.FormValue("id"))
	var info struct{ Config struct{ Tty bool } }
	if err := h.get(r.Context(), id+"/json", &info); err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	resp, err := h.call(r.Context(), "GET", id+"/logs?follow=1&stdout=1&stderr=1&tail=500")
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	var body io.Reader = resp.Body
	if !info.Config.Tty {
		pr, pw := io.Pipe()
		go func() { pw.CloseWithError(demux(pw, resp.Body)) }()
		body = pr
	}
	w.Header().Set("Content-Type", "text/event-stream")
	rc := http.NewResponseController(w)
	sc := bufio.NewScanner(body)
	sc.Buffer(nil, 1<<20)
	for sc.Scan() {
		fmt.Fprintf(w, "data: %s\n\n", strings.ReplaceAll(sc.Text(), "\r", ""))
		rc.Flush()
	}
}

// demux strips the 8-byte frame headers Docker puts in the log of a
// container without a TTY: stream type, three zero bytes, big-endian length.
func demux(dst io.Writer, src io.Reader) error {
	hdr := make([]byte, 8)
	for {
		if _, err := io.ReadFull(src, hdr); err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}
		if _, err := io.CopyN(dst, src, int64(binary.BigEndian.Uint32(hdr[4:]))); err != nil {
			return err
		}
	}
}

// spark draws CPU history in block characters, scaled to its peak but to
// no less than 10%, so an idle container stays flat.
func spark(hist []float64) string {
	bars := []rune("▁▂▃▄▅▆▇█")
	top := 10.0
	for _, v := range hist {
		top = max(top, v)
	}
	var b strings.Builder
	for _, v := range hist {
		b.WriteRune(bars[min(len(bars)-1, int(v/top*float64(len(bars)-1)+0.5))])
	}
	return b.String()
}

func size(b uint64) string {
	if b >= 1<<30 {
		return fmt.Sprintf("%.1fG", float64(b)/(1<<30))
	}
	return fmt.Sprintf("%dM", b>>20)
}

// guard asks for the password. A browser sends basic auth along with
// cross-site requests too, so changes only come from the panel's own pages.
func guard(password string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, pw, _ := r.BasicAuth()
		if subtle.ConstantTimeCompare([]byte(pw), []byte(password)) != 1 {
			w.Header().Set("WWW-Authenticate", `Basic realm="kuzyo"`)
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		if r.Method != "GET" && r.Header.Get("Sec-Fetch-Site") != "same-origin" {
			http.Error(w, "cross-site request", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func main() {
	addr := flag.String("addr", "127.0.0.1:8090", "listen address")
	flag.Usage = func() {
		fmt.Fprintln(os.Stderr, "usage: panel [-addr host:port] name=/path/to/docker.sock ...")
		flag.PrintDefaults()
	}
	flag.Parse()
	password := os.Getenv("PANEL_PASSWORD")
	if password == "" {
		log.Fatal("PANEL_PASSWORD is empty")
	}
	p := &panel{prev: map[string]sample{}, hist: map[string][]float64{}}
	for _, arg := range flag.Args() {
		name, sock, ok := strings.Cut(arg, "=")
		if !ok {
			flag.Usage()
			os.Exit(2)
		}
		p.hosts = append(p.hosts, newHost(name, sock))
	}
	if len(p.hosts) == 0 {
		flag.Usage()
		os.Exit(2)
	}

	go func() {
		for {
			p.poll()
			time.Sleep(interval)
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", p.index)
	mux.HandleFunc("POST /action", p.action)
	mux.HandleFunc("GET /logs", p.logs)
	mux.HandleFunc("GET /logs/stream", p.logStream)
	log.Printf("listening on %s", *addr)
	log.Fatal(http.ListenAndServe(*addr, guard(password, mux)))
}
