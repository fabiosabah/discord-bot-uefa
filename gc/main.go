package main

import (
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	dota2 "github.com/paralin/go-dota2"
	"github.com/paralin/go-steam"
	"github.com/paralin/go-steam/protocol/steamlang"
	"github.com/sirupsen/logrus"
)

func main() {
	username := os.Getenv("STEAM_USERNAME")
	password := os.Getenv("STEAM_PASSWORD")
	twoFactorCode := os.Getenv("STEAM_2FA_CODE") // código TOTP manual (opcional)

	if username == "" || password == "" {
		log.Fatal("STEAM_USERNAME e STEAM_PASSWORD são obrigatórios")
	}

	logger := logrus.New()
	logger.SetFormatter(&logrus.TextFormatter{FullTimestamp: true})
	logger.SetLevel(logrus.InfoLevel)

	client := steam.NewClient()
	var dotaClient *dota2.Dota2

	// Loop de eventos do Steam (roda em goroutine separada)
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
				// Reutiliza sentry de logins anteriores (Steam Guard por e-mail)
				if sentry, err := os.ReadFile("sentry.bin"); err == nil {
					details.SentryFileHash = sentry
					logger.Info("[Steam] Sentry encontrado, usando para login")
				}
				client.Auth.LogOn(details)

			case *steam.LoggedOnEvent:
				logger.WithField("steamid", client.SteamId()).Info("[Steam] Login OK")
				client.Social.SetPersonaState(steamlang.EPersonaState_Online)

				logger.Info("[GC] Inicializando cliente Dota 2...")
				dotaClient = dota2.New(client, logger)
				dotaClient.SetPlaying(true)

				// Aguarda Steam confirmar o SetPlaying antes de enviar o Hello
				time.Sleep(3 * time.Second)
				dotaClient.SayHello()
				logger.Info("[GC] SayHello enviado — aguardando sessão com o Game Coordinator...")

			case *steam.LogOnFailedEvent:
				logger.WithField("result", e.Result).Fatal("[Steam] Login falhou")

			case *steam.MachineAuthUpdateEvent:
				// Steam Guard por e-mail: salva o sentry para logins futuros
				if err := os.WriteFile("sentry.bin", e.Hash, 0600); err != nil {
					logger.WithError(err).Warn("[Steam] Não foi possível salvar sentry")
				} else {
					logger.Info("[Steam] Sentry salvo em sentry.bin — próximos logins serão automáticos")
				}

			case *steam.DisconnectedEvent:
				logger.Warn("[Steam] Desconectado")
				dotaClient = nil

			case steam.FatalErrorEvent:
				logger.Fatalf("[Steam] Erro fatal: %v", e)

			case error:
				logger.WithError(e).Error("[Steam] Erro")
			}
		}
	}()

	logger.Info("[Steam] Conectando ao Steam...")
	client.Connect()

	// Aguarda SIGINT/SIGTERM para encerrar limpo
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop

	logger.Info("Encerrando...")
	if dotaClient != nil {
		dotaClient.SetPlaying(false)
	}
	client.Disconnect()
}
