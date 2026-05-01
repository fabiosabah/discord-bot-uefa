package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	dota2 "github.com/paralin/go-dota2"
	devents "github.com/paralin/go-dota2/events"
	"github.com/paralin/go-dota2/protocol"
	"github.com/paralin/go-steam"
	"github.com/paralin/go-steam/protocol/steamlang"
	"github.com/sirupsen/logrus"
)

type LobbyInfo struct {
	Name     string `json:"name"`
	Password string `json:"password"`
}

type App struct {
	mu      sync.RWMutex
	gcReady bool
	dota    *dota2.Dota2
	lobby   *LobbyInfo
	logger  *logrus.Logger
}

func (a *App) setGCReady(ready bool) {
	a.mu.Lock()
	a.gcReady = ready
	a.mu.Unlock()
}

func (a *App) setDota(d *dota2.Dota2) {
	a.mu.Lock()
	a.dota = d
	a.mu.Unlock()
}

func (a *App) handleStatus(w http.ResponseWriter, r *http.Request) {
	a.mu.RLock()
	ready := a.gcReady
	lobby := a.lobby
	a.mu.RUnlock()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"gc_ready": ready,
		"lobby":    lobby,
	})
}

func (a *App) handleCreateLobby(w http.ResponseWriter, r *http.Request) {
	a.mu.RLock()
	ready := a.gcReady
	d := a.dota
	a.mu.RUnlock()

	if !ready || d == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "GC não está pronto"})
		return
	}

	var req struct {
		Name     string `json:"name"`
		Password string `json:"password"`
		Mode     uint32 `json:"mode"`
		Region   uint32 `json:"region"`
	}
	req.Mode = 1   // All Pick
	req.Region = 7 // South America

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil && err.Error() != "EOF" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "body inválido"})
		return
	}
	if req.Name == "" {
		req.Name = "Liga Fumos"
	}

	vis := protocol.DOTALobbyVisibility_DOTALobbyVisibility_Public
	allowSpec := true
	details := &protocol.CMsgPracticeLobbySetDetails{
		GameName:        &req.Name,
		PassKey:         &req.Password,
		ServerRegion:    &req.Region,
		GameMode:        &req.Mode,
		AllowSpectating: &allowSpec,
		Visibility:      &vis,
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	a.logger.WithField("name", req.Name).Info("[GC] Criando lobby...")
	if err := d.LeaveCreateLobby(ctx, details, true); err != nil {
		a.logger.WithError(err).Error("[GC] Falha ao criar lobby")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	a.mu.Lock()
	a.lobby = &LobbyInfo{Name: req.Name, Password: req.Password}
	a.mu.Unlock()

	a.logger.WithField("name", req.Name).Info("[GC] Lobby criado com sucesso")
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"ok":       true,
		"name":     req.Name,
		"password": req.Password,
	})
}

func (a *App) handleDestroyLobby(w http.ResponseWriter, r *http.Request) {
	a.mu.RLock()
	ready := a.gcReady
	d := a.dota
	a.mu.RUnlock()

	if !ready || d == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "GC não está pronto"})
		return
	}

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	if _, err := d.DestroyLobby(ctx); err != nil {
		a.logger.WithError(err).Error("[GC] Falha ao destruir lobby")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	a.mu.Lock()
	a.lobby = nil
	a.mu.Unlock()

	a.logger.Info("[GC] Lobby destruído")
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"ok": true})
}

func main() {
	username := os.Getenv("STEAM_USERNAME")
	password := os.Getenv("STEAM_PASSWORD")
	twoFactorCode := os.Getenv("STEAM_2FA_CODE")
	httpPort := os.Getenv("GC_HTTP_PORT")
	if httpPort == "" {
		httpPort = "8080"
	}

	if username == "" || password == "" {
		log.Fatal("STEAM_USERNAME e STEAM_PASSWORD são obrigatórios")
	}

	logger := logrus.New()
	logger.SetFormatter(&logrus.TextFormatter{FullTimestamp: true})
	logger.SetLevel(logrus.InfoLevel)

	app := &App{logger: logger}
	client := steam.NewClient()

	go func() {
		for event := range client.Events() {
			switch e := event.(type) {

			case *steam.ConnectedEvent:
				logger.Info("[Steam] Conectado — fazendo login...")
				details := &steam.LogOnDetails{
					Username:      username,
					Password:      password,
					TwoFactorCode: twoFactorCode,
				}
				if sentry, err := os.ReadFile("sentry.bin"); err == nil {
					details.SentryFileHash = sentry
					logger.Info("[Steam] Sentry encontrado")
				}
				client.Auth.LogOn(details)

			case *steam.LoggedOnEvent:
				logger.WithField("steamid", client.SteamId()).Info("[Steam] Login OK")
				client.Social.SetPersonaState(steamlang.EPersonaState_Online)

				logger.Info("[GC] Inicializando cliente Dota 2...")
				d := dota2.New(client, logger)
				app.setDota(d)
				d.SetPlaying(true)
				time.Sleep(3 * time.Second)
				d.SayHello()
				logger.Info("[GC] SayHello enviado — aguardando sessão...")

			case *steam.LogOnFailedEvent:
				logger.WithField("result", e.Result).Fatal("[Steam] Login falhou")

			case *steam.MachineAuthUpdateEvent:
				if err := os.WriteFile("sentry.bin", e.Hash, 0600); err != nil {
					logger.WithError(err).Warn("[Steam] Não foi possível salvar sentry")
				} else {
					logger.Info("[Steam] Sentry salvo em sentry.bin")
				}

			case *steam.DisconnectedEvent:
				logger.Warn("[Steam] Desconectado")
				app.setDota(nil)
				app.setGCReady(false)

			case steam.FatalErrorEvent:
				logger.Fatalf("[Steam] Erro fatal: %v", e)

			case *devents.ClientWelcomed:
				logger.Info("[GC] Sessão estabelecida (ClientWelcomed) — GC pronto")
				app.setGCReady(true)

			case *devents.GCConnectionStatusChanged:
				if e.NewState == protocol.GCConnectionStatus_GCConnectionStatus_HAVE_SESSION {
					logger.Info("[GC] GC pronto (HAVE_SESSION)")
					app.setGCReady(true)
				} else {
					logger.WithField("state", e.NewState.String()).Warn("[GC] GC não disponível")
					app.setGCReady(false)
				}

			case error:
				logger.WithError(e).Error("[Steam] Erro")
			}
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("/status", app.handleStatus)
	mux.HandleFunc("/lobby", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPost:
			app.handleCreateLobby(w, r)
		case http.MethodDelete:
			app.handleDestroyLobby(w, r)
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})

	srv := &http.Server{Addr: ":" + httpPort, Handler: mux}
	go func() {
		logger.WithField("port", httpPort).Info("[HTTP] Servidor iniciado")
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.WithError(err).Fatal("[HTTP] Falha ao iniciar servidor")
		}
	}()

	logger.Info("[Steam] Conectando ao Steam...")
	client.Connect()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop

	logger.Info("Encerrando...")
	shutCtx, shutCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer shutCancel()
	srv.Shutdown(shutCtx)

	app.mu.RLock()
	d := app.dota
	app.mu.RUnlock()
	if d != nil {
		d.SetPlaying(false)
	}
	client.Disconnect()
}
