package dota

import (
	"context"

	dota2 "github.com/paralin/go-dota2"
	"github.com/paralin/go-dota2/protocol"
	"github.com/sirupsen/logrus"
)

type LobbyRequest struct {
	Name     string
	Password string
	Mode     uint32 // 1 = All Pick
	Region   uint32 // 7 = South America
}

type Client struct {
	d      *dota2.Dota2
	logger *logrus.Logger
}

func New(d *dota2.Dota2, logger *logrus.Logger) *Client {
	logger.Info("[Dota] Cliente Dota 2 GC inicializado")
	return &Client{d: d, logger: logger}
}

func (c *Client) CreateLobby(ctx context.Context, req LobbyRequest) error {
	c.logger.WithFields(logrus.Fields{
		"name":   req.Name,
		"mode":   req.Mode,
		"region": req.Region,
	}).Info("[Dota] Criando lobby...")

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

	if err := c.d.LeaveCreateLobby(ctx, details, true); err != nil {
		c.logger.WithError(err).Error("[Dota] Falha ao criar lobby")
		return err
	}

	c.logger.WithField("name", req.Name).Info("[Dota] Lobby criado com sucesso")
	return nil
}

func (c *Client) DestroyLobby(ctx context.Context) error {
	c.logger.Info("[Dota] Destruindo lobby...")

	if _, err := c.d.DestroyLobby(ctx); err != nil {
		c.logger.WithError(err).Error("[Dota] Falha ao destruir lobby")
		return err
	}

	c.logger.Info("[Dota] Lobby destruído")
	return nil
}
