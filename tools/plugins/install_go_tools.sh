#!/bin/bash -x

. /vagrant/conf.sh

set -e

apt-get install -y -q \
    golang


export GOPATH=/project/sawtooth-core/sdk/go

go get -u \
    github.com/golang/protobuf/proto \
    github.com/golang/protobuf/protoc-gen-go \
    github.com/pebbe/zmq4 \
    github.com/brianolson/cbor_go \
    github.com/satori/go.uuid

chown -R $VAGRANT_USER:$VAGRANT_USER $GOPATH
chown -R $VAGRANT_USER:$VAGRANT_USER $GOPATH/bin
