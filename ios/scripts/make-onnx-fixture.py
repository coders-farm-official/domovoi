#!/usr/bin/env python3
"""Writes tiny_add.onnx, the package tests' ONNX Runtime fixture.

One node, `Z = Add(X, Y)`, over float tensors of shape [3]. Written here, by
hand, in protobuf's wire format, so the fixture is ours (MIT, like the rest
of the repo) and comes from no model zoo, no download and no `onnx` package:
the bytes are exactly what this file says.

    python3 ios/scripts/make-onnx-fixture.py \
        ios/Packages/DomovoiKit/Tests/DomovoiKitTests/Fixtures/tiny_add.onnx

`OnnxRuntimeLinkTests` loads it through ONNX Runtime and checks
[1, 2, 3] + [10, 20, 30] = [11, 22, 33].
"""
import sys


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def key(field, wire):
    return varint((field << 3) | wire)


def f_varint(field, n):
    return key(field, 0) + varint(n)


def f_bytes(field, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return key(field, 2) + varint(len(data)) + data


FLOAT = 1  # TensorProto.DataType.FLOAT


def tensor_info(name, length):
    dim = f_varint(1, length)                       # Dimension.dim_value
    shape = f_bytes(1, dim)                         # TensorShapeProto.dim
    tensor = f_varint(1, FLOAT) + f_bytes(2, shape)  # TypeProto.Tensor
    type_proto = f_bytes(1, tensor)                 # TypeProto.tensor_type
    return f_bytes(1, name) + f_bytes(2, type_proto)  # ValueInfoProto


def model():
    node = (
        f_bytes(1, "X") + f_bytes(1, "Y")  # NodeProto.input
        + f_bytes(2, "Z")                  # NodeProto.output
        + f_bytes(3, "add")                # NodeProto.name
        + f_bytes(4, "Add")                # NodeProto.op_type
    )
    graph = (
        f_bytes(1, node)                       # GraphProto.node
        + f_bytes(2, "tiny_add")               # GraphProto.name
        + f_bytes(11, tensor_info("X", 3))     # GraphProto.input
        + f_bytes(11, tensor_info("Y", 3))
        + f_bytes(12, tensor_info("Z", 3))     # GraphProto.output
    )
    opset = f_varint(2, 13)                    # OperatorSetIdProto.version (domain "")
    return (
        f_varint(1, 8)                         # ModelProto.ir_version
        + f_bytes(2, "domovoi-fixture")        # ModelProto.producer_name
        + f_bytes(7, graph)                    # ModelProto.graph
        + f_bytes(8, opset)                    # ModelProto.opset_import
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: make-onnx-fixture.py OUT.onnx")
    with open(sys.argv[1], "wb") as out:
        out.write(model())
