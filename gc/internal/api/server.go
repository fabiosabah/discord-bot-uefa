package api

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/sirupsen/logrus"

	"liga-discord-gc/internal/app"
	"liga-discord-gc/internal/dota"
)

// DotaClientFunc returns the current Dota client (may be nil).
type DotaClientFunc func() *dota.Client

type Server struct {
	app        *app.App
	getDota    DotaClientFunc
	logger     *logrus.Logger
	httpServer *http.Server
}

func New(port string, a *app.App, getDota DotaClientFunc, logger *logrus.Logger) *Server {
	s := &Server{
		app:     a,
		getDota: getDota,
		logger:  logger,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/status", s.handleStatus)
	mux.HandleFunc("/lobby", s.handleLobby)

	s.httpServer = &http.Server{
		Addr:    ":" + port,
		Handler: mux,
	}

	return s
}

func (s *Server) Start() {
	s.logger.WithField("addr", s.httpServer.Addr).Info("[API] Servidor HTTP iniciado")
	if err := s.httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		s.logger.WithError(err).Fatal("[API] Falha no servidor HTTP")
	}
}

func (s *Server) Shutdown(ctx context.Context) {
	s.logger.Info("[API] Encerrando servidor HTTP...")
	if err := s.httpServer.Shutdown(ctx); err != nil {
		s.logger.WithError(err).Warn("[API] Erro ao encerrar servidor HTTP")
	}
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	s.logger.Debug("[API] GET /status")

	gcReady := s.app.IsGCReady()
	lobby := s.app.GetLobby()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"gc_ready": gcReady,
		"lobby":    lobby,
	})
}

func (s *Server) handleLobby(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodPost:
		s.handleCreateLobby(w, r)
	case http.MethodDelete:
		s.handleDestroyLobby(w, r)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *Server) handleCreateLobby(w http.ResponseWriter, r *http.Request) {
	s.logger.Info("[API] POST /lobby — solicitação de criação de lobby recebida")

	if !s.app.IsGCReady() {
		s.logger.Warn("[API] POST /lobby rejeitado — GC não está pronto")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "GC não está pronto"})
		return
	}

	d := s.getDota()
	if d == nil {
		s.logger.Warn("[API] POST /lobby rejeitado — cliente Dota não disponível")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "cliente Dota não disponível"})
		return
	}

	var req struct {
		Name     string `json:"name"`
		Password string `json:"password"`
		Mode     uint32 `json:"mode"`
		Region   uint32 `json:"region"`
	}
	req.Mode = 1
	req.Region = 7

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil && err.Error() != "EOF" {
		s.logger.WithError(err).Warn("[API] POST /lobby — body inválido")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "body JSON inválido"})
		return
	}
	if req.Name == "" {
		req.Name = "Liga Fumos"
	}

	s.logger.WithFields(logrus.Fields{
		"name":   req.Name,
		"mode":   req.Mode,
		"region": req.Region,
	}).Info("[API] Encaminhando criação de lobby ao cliente Dota...")

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()

	if err := d.CreateLobby(ctx, dota.LobbyRequest{
		Name:     req.Name,
		Password: req.Password,
		Mode:     req.Mode,
		Region:   req.Region,
	}); err != nil {
		s.logger.WithError(err).Error("[API] POST /lobby falhou")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	s.app.SetLobby(&app.LobbyInfo{Name: req.Name, Password: req.Password})
	s.logger.WithField("name", req.Name).Info("[API] Lobby criado e registrado no estado da app")

	// Aguarda o GC sincronizar o estado do lobby antes de mover o bot
	time.Sleep(2 * time.Second)
	s.logger.Info("[API] Movendo bot para slot de spectator...")
	d.JoinAsSpectator()

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"ok":       true,
		"name":     req.Name,
		"password": req.Password,
	})
}

func (s *Server) handleDestroyLobby(w http.ResponseWriter, r *http.Request) {
	s.logger.Info("[API] DELETE /lobby — solicitação de encerramento de lobby recebida")

	if !s.app.IsGCReady() {
		s.logger.Warn("[API] DELETE /lobby rejeitado — GC não está pronto")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "GC não está pronto"})
		return
	}

	d := s.getDota()
	if d == nil {
		s.logger.Warn("[API] DELETE /lobby rejeitado — cliente Dota não disponível")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"error": "cliente Dota não disponível"})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()

	if err := d.DestroyLobby(ctx); err != nil {
		s.logger.WithError(err).Error("[API] DELETE /lobby falhou")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	s.app.SetLobby(nil)
	s.logger.Info("[API] Lobby destruído e removido do estado da app")

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"ok": true})
}
