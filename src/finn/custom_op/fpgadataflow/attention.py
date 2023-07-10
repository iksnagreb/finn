# Python warning subsystem
import warnings
# Numpy math and arrays
import numpy as np
# Derive custom operators form the FINN base custom op
# from finn.custom_op.fpgadataflow.hlscustomop import HLSCustomOp
# Temporarily derive custom operators from QONNX base custom op
#   TODO: Remove once switching to HLSCustomOp
from qonnx.custom_op.base import CustomOp

# QONNX/FINN datatypes
from qonnx.core.datatype import DataType


# Scaled Dot-Product Attention Custom Operator
#   Note: Single head attention
class ScaledDotProductAttention(CustomOp):
    # Initializes the operator given an onnx graph node
    def __init__(self, onnx_node, **kwargs):
        # Just forward all arguments to the init method of the CustomOp base
        super().__init__(onnx_node, **kwargs)

    # Returns a dict of permitted attributes for the node
    def get_nodeattr_types(self):
        # Start from parent operator class attributes
        attrs = {}  # super().get_nodeattr_types()
        # Update attributes dictionary for new custom operator
        attrs.update({
            # Shared query and key and value embedding dimension
            "qk_dim": ("i", True, 0),
            "v_dim": ("i", True, 0),
            # Shared key and value and query sequence length
            "kv_len": ("i", True, 0),
            "q_len": ("i", True, 0),
            # Datatypes of inputs and outputs
            "q_dtype": ("s", True, ""),
            "k_dtype": ("s", True, ""),
            "v_dtype": ("s", True, ""),
            "o_dtype": ("s", True, ""),
            # Mode used for providing the attention mask
            #  There can be no mask, a mask sent as the fourth input or a causal
            #  attention mask which is generated by the operator itself.
            "mask_mode": ("s", True, "none", {"none", "input", "causal"}),
            # Datatype of the attention mask (if there is a mask)
            "mask_dtype": ("s", False, ""),
            # Input (SIMD) and output (PE) parallelism
            "SIMD": ("i", True, 0),
            "PE": ("i", True, 0),
            # Execution mode of the operator
            # TODO: Remove once switching to HLSCustomOp
            "exec_mode": ("s", False, "", {"", "rtlsim", "cppsim", "python"}),
        })
        # Return updated attribute dictionary
        return attrs

    # Validates the attention shape constraints on queries, keys and values
    @staticmethod
    def assert_shape_constraints(q, k, v, mask=None, pe=None, simd=None):
        # Queries and keys must match in embedding (second) dimension
        assert q.shape[1] == k.shape[1], \
            "Queries and Keys must have matching embedding dimension"
        # Keys and values must have matching sequence length (first) dimension
        #   Note: Lifting the restriction of q matching as well allows for cross
        #       attention using the same operator.
        assert k.shape[0] == v.shape[0], \
            "Keys and Values must have matching sequence length"

        # If the mask is provided, it must have a shape matching the query and
        # key product shape, i.e. the shape of the attention matrix
        if mask is not None and not isinstance(mask, str):
            # Compare mask shape against attention matrix shape
            assert mask.shape == (q.shape[0], k.shape[0]), \
                "Mask shape must match the shape of the attention matrix"

        # If specified, validate PE shape constraints as well
        if pe is not None:
            # PE operates along the sequence length dimension of the keys
            assert k.shape[0] % pe == 0, \
                "Key sequence length must be divisible by PE"
            # PE operates along the embedding dimension of the values
            assert v.shape[1] % pe == 0, \
                "Value embedding dimension must be divisible by PE"

        # If specified, validate SIMD shape constraints as well
        if simd is not None:
            # SIMD operates along the shared embedding dimension of the queries
            # and the keys
            assert q.shape[1] % simd == 0, \
                "Query and Key embedding dimension must be divisible by SIMD"
            # SIMD operates along the sequence length dimension of the values
            assert v.shape[0] % simd == 0, \
                "Value sequence length must be divisible by SIMD"

    # Generates a dummy node matching the shapes of the input numpy arrays
    @staticmethod
    def make_modelwrapper_like(q, k, v, mask=None, pe=1, simd=1, **dtypes):
        # Utility types and function for creating onnx nodes and graphs
        from onnx import TensorProto, helper
        # Utility for creating and wrapping qonnx graphs and models
        from qonnx.util.basic import qonnx_make_model
        from qonnx.core.modelwrapper import ModelWrapper

        # Convert unspecified mask to 'none' mode
        mask = 'none' if mask is None else mask
        # Validate all shape constraints first
        ScaledDotProductAttention.assert_shape_constraints(
            q, k, v, mask, pe, simd
        )

        # Start building the node as a dictionary of attributes
        node_kwargs = {
            # Refer to this operator type by its name
            "op_type": "ScaledDotProductAttention",
            # Execution will try to look up the implementation in the package
            # referred to by the domain
            #   Note: the op_type should be registered as a custom op within the
            #       domain package
            "domain": "finn.custom_op.fpgadataflow",
            # Execution backend
            #   Note: Required attribute inherited from HLSCustomOp
            "backend": "fpgadataflow",
            # Configuration of input parallelism
            "SIMD": simd,
            # Configuration of output parallelism
            "PE": pe
        }

        # Infer the output shape from the input shapes
        o_shape = (q.shape[0], v.shape[1])

        # Create onnx value info of all inputs and outputs assuming float
        # datatypes
        q_info = helper.make_tensor_value_info("q", TensorProto.FLOAT, q.shape)
        k_info = helper.make_tensor_value_info("k", TensorProto.FLOAT, k.shape)
        v_info = helper.make_tensor_value_info("v", TensorProto.FLOAT, v.shape)
        o_info = helper.make_tensor_value_info("o", TensorProto.FLOAT, o_shape)

        # Collect input and output nodes in order
        inputs, outputs = [q_info, k_info, v_info], [o_info]

        # Collect all inputs/outputs to the operator node
        io_kwargs = {
            "inputs": ["q", "k", "v"], "outputs": ["o"], "mask_mode": "none"
        }

        # Start building the shape attributes
        shape_kwargs = {
            # Shared embedding dimension of the queries and keys and embedding
            # dimension of the values
            "qk_dim": q.shape[1], "v_dim": v.shape[1],
            # Shared sequence length of keys and values and sequence length of
            # the queries
            "kv_len": k.shape[0], "q_len": q.shape[0],
        }

        # Start building the datatype attributes
        dtype_kwargs = {
            # Datatypes of the query, key, value inputs and the output
            "q_dtype": "FLOAT32", "k_dtype": "FLOAT32",
            "v_dtype": "FLOAT32", "o_dtype": "FLOAT32",
        }

        # If the optional mask is specified as an input
        if isinstance(mask, np.ndarray) or mask == "input":
            # Add the mask to the input node names
            io_kwargs["inputs"].append("mask")
            # Configure masking mode via io_kwargs as well
            io_kwargs["mask_mode"] = "input"
            # Always infer the mask shape
            mask_shape = (q.shape[0], k.shape[0])
            # Create value info of the mask input
            mask_info = helper.make_tensor_value_info(
                "mask", TensorProto.FLOAT, mask_shape
            )
            # Append the mask input as fourth input node
            inputs.append(mask_info)
            # Add the mask default datatype to the datatype attributes
            dtype_kwargs["mask_dtype"] = "FLOAT32"

        # If a causal mask is to be generated during execution
        if mask == "causal":
            # Configure masking mode via io_kwargs as well
            io_kwargs["mask_mode"] = "causal"
            # Add the mask default datatype to the datatype attributes
            dtype_kwargs["mask_dtype"] = "FLOAT32"

        # The optional dtypes keyword arguments must describe a subset of the
        # model inputs and outputs
        assert set(dtypes) <= {*dtype_kwargs, "mask_dtype"}, \
            "Specified datatype of unknown input or output"

        # Update the datatype attributes according to the keyword arguments
        dtype_kwargs.update({
            key: value.name for key, value in dtypes.items()
        })

        # Create an onnx graph node by unpacking all prepared keyword arguments
        node = helper.make_node(
            **node_kwargs, **io_kwargs, **shape_kwargs, **dtype_kwargs
        )
        # Create a graph out of the operator node and the input/output nodes
        graph = helper.make_graph(
            [node], inputs=inputs, outputs=outputs, name='attention_graph'
        )
        # Wrap the graph in a qonnx model wrapper
        model = ModelWrapper(qonnx_make_model(
            graph, producer_name='attention-model'
        ))

        # Add datatype annotations to all input tensors
        for tensor_name in io_kwargs["inputs"]:
            # Only annotate if a datatype is specified
            if f'{tensor_name}_dtype' in dtypes:
                # Update the datatype annotation
                model.set_tensor_datatype(
                    tensor_name, dtypes[f'{tensor_name}_dtype']
                )

        # Add datatype annotations to all output tensors
        for tensor_name in io_kwargs["outputs"]:
            # Only annotate if a datatype is specified
            if f'{tensor_name}_dtype' in dtypes:
                # Update the datatype annotation
                model.set_tensor_datatype(
                    tensor_name, dtypes[f'{tensor_name}_dtype']
                )

        # Return the constructed qonnx model wrapper
        return model

    # Returns an ONNX node that has the same shape inference behavior
    def make_shape_compatible_op(self, model):
        # Infer the output shape from the input shapes
        o_shape = (self.get_nodeattr("q_len"), self.get_nodeattr("v_dim"))
        # Constant operation producing output of given shape
        return super().make_const_shape_op(o_shape)

    # Infers the output data types and updates the input datatypes of the node
    def infer_node_datatype(self, model):
        # ONNX graph node of the operator
        node = self.onnx_node

        # Get input datatypes from model for query, key, value nodes in order
        q_dtype = model.get_tensor_datatype(node.input[0])
        k_dtype = model.get_tensor_datatype(node.input[1])
        v_dtype = model.get_tensor_datatype(node.input[2])

        # Test for changing query input datatype
        if q_dtype != self.get_nodeattr("q_dtype"):
            # Issue a warning message
            warnings.warn("q_dtype changing for %s: %s -> %s " % (
                node.name,
                str(self.get_nodeattr("q_dtype")),
                str(q_dtype),
            ))
        # Test for changing key input datatype
        if k_dtype != self.get_nodeattr("k_dtype"):
            # Issue a warning message
            warnings.warn("k_dtype changing for %s: %s -> %s " % (
                node.name,
                str(self.get_nodeattr("k_dtype")),
                str(k_dtype),
            ))
        # Test for changing value input datatype
        if v_dtype != self.get_nodeattr("v_dtype"):
            # Issue a warning message
            warnings.warn("v_dtype changing for %s: %s -> %s " % (
                node.name,
                str(self.get_nodeattr("v_dtype")),
                str(v_dtype),
            ))

        # Update the node datatype attributes
        self.set_nodeattr("q_dtype", q_dtype.name)
        self.set_nodeattr("k_dtype", k_dtype.name)
        self.set_nodeattr("v_dtype", v_dtype.name)

        # Attention mask might be provided as an input as well
        if self.get_nodeattr("mask_mode") == "input":
            # Get the datatype attribute of the attention mask
            #   Note: Optional mask will be provided as the fourth input
            mask_dtype = model.get_tensor_datatype(node.input[3])
            # Test for changing mask input datatype
            if mask_dtype != self.get_nodeattr("mask_dtype"):
                # Issue a warning message
                warnings.warn("mask_dtype changing for %s: %s -> %s " % (
                    node.name,
                    str(self.get_nodeattr("mask_dtype")),
                    str(mask_dtype),
                ))
            # Update the node datatype attribute of the attention mask
            self.set_nodeattr("mask_dtype", mask_dtype.namke)

        # Set the model output datatype
        model.set_tensor_datatype(node.output[0], self.get_nodeattr('o_dtype'))

    # Executes the node
    def execute_node(self, context, graph):
        # Get the mode to use for execution
        mode = self.get_nodeattr("exec_mode")

        # Support python execution mode for now
        # TODO: Remove python mode once switching to HLSCustomOp
        if mode == "python":
            # Numpy compatible softmax implementation
            from scipy.special import softmax
            # Generate random input data for testing
            from qonnx.util.basic import gen_finn_dt_tensor

            # Read input tensors of the query, key and value inputs from context
            q = context[self.onnx_node.input[0]]
            k = context[self.onnx_node.input[1]]
            v = context[self.onnx_node.input[2]]
            # Get the shared embedding dimension of queries and keys
            d = self.get_nodeattr('qk_dim')
            # Start with zero mask
            mask = 0
            # The actual attention mask may be provided as the fourth input
            if self.get_nodeattr("mask_mode") == "input":
                # Get the mask tensor from the execution context
                mask = context[self.onnx_node.input[3]]
            # Another option is to generate a causal attention mask on the fly
            elif self.get_nodeattr("mask_mode") == "causal":
                # Get the datatype of the attention mask
                mask_dtype = DataType[self.get_nodeattr("mask_dtype")]
                # Start with an all zero attention mask
                mask = 0 * gen_finn_dt_tensor(
                    mask_dtype, (q.shape[0], k.shape[0])
                )
                # Generate upper triangular causal attention mask
                mask[np.triu_indices_from(mask, 1)] = - np.inf
            # Compute the attention matrix between queries and keys
            attention = softmax(q @ k.T * (d ** -0.5) + mask, axis=-1)
            # Compute product of attention weights and value input
            o = attention @ v
            # Get the name of the output
            o_name = self.onnx_node.output[0]
            # Save the output tensor to the execution context
            context[o_name] = o
        # CPP Simulation of the HLS operator
        elif mode == "cppsim":
            # TODO: Implement cppsim mode
            raise NotImplementedError(
                "exec_mode cppsim is not implemented yet!"
            )
        # RTL Simulation of the HLS operator
        elif mode == "rtlsim":
            # TODO: Implement rtlsim mode
            raise NotImplementedError(
                "exec_mode rtlsim is not implemented yet!"
            )
        # All other modes are unsupported
        else:
            raise Exception(
                """
                Invalid value for attribute exec_mode! Is currently set to: {}
                has to be set to one of the following value ("cppsim", "rtlsim")
                """.format(mode)
            )

    # Optional node verification
    def verify_node(self):
        pass

    # Gets the datatype of input at index ind
    def get_input_datatype(self, ind=0):
        # Ordered list of names of allowed inputs
        inputs = ["q", "k", "v"]
        # If the attention mask is provided as input, it has a type as well
        if self.get_nodeattr("mask_mode") == "input":
            # The mask type is an attribute itself
            inputs += ["mask"]
        # Look up datatype name in attributes and convert to DataType
        return DataType[self.get_nodeattr(f"{inputs[ind]}_dtype")]

    # Gets the datatype of the output (at index ind, but there is just one)
    def get_output_datatype(self, ind=0):
        # Ordered list of names of allowed outputs
        outputs = ["o"]
        # Look up datatype name in attributes and convert to DataType
        return DataType[self.get_nodeattr(f"{outputs[ind]}_dtype")]

    # Gets the shape of the input at index ind without folding
    def get_normal_input_shape(self, ind=0):
        # List shapes of inputs in order
        inputs_shapes = [
            # Query input sequence
            (self.get_nodeattr("q_len"), self.get_nodeattr("qk_dim")),
            # Key input sequence
            (self.get_nodeattr("kv_len"), self.get_nodeattr("kv_dim")),
            # Value input sequence
            (self.get_nodeattr("kv_len"), self.get_nodeattr("v_dim")),
        ]
        # If the attention mask is provided as input, it has a shape as well
        if self.get_nodeattr("mask_mode") == "input":
            # Mask shape is inferred from query and key sequence lengths
            inputs_shapes += [
                (self.get_nodeattr("q_len"), self.get_nodeattr("kv_len"))
            ]
        # Get the shape by indexing into the ordered list of all inputs
        return inputs_shapes[ind]

    # Gets the shape of the output at index ind (there is just one) without
    # folding
    def get_normal_output_shape(self, ind=0):  # noqa, there is just one output
        # The output shape is inferred from the length of the query sequence and
        # the embedding dimension of the values
        return tuple((self.get_nodeattr("q_len"), self.get_nodeattr("v_dim")))

    # Gets the shape of the input at index ind with folding
    def get_folded_input_shape(self, ind=0):
        # Get the unfolded size of the input
        t, d = self.get_normal_input_shape(ind)
        # Get the amount of input (SIMD) and output (PE) parallelism
        simd = self.get_nodeattr("SIMD")
        pe = self.get_nodeattr("PE")  # TODO: What about this?

        # The query (first) and key (second) inputs are treated the same and
        # merely differ in buffering requirements
        if ind == 0 or ind == 1:
            # Fold the input along the embedding dimension
            sf = d // simd
            # New shape with SIMD elements as the last dimension
            return tuple((t, sf, simd))
        # For the value (third) inputs the axes flip and simd/pe change roles
        if ind == 2:
            # Fold the input along the sequence length dimension
            sf = t // simd
            # New shape with SIMD elements as the last dimension
            return tuple((sf, d, simd))
        # If the mask is provided as input, it is folded as well
        if ind == 3 and self.get_nodeattr("mask_mode") == "input":
            # The mask is folded along the second dimension which is actually a
            # sequence length as well. It might be confusing to call it d here.
            sf = d // simd
            # New shape with SIMD elements as the last dimension
            return tuple((t, sf, simd))

        # If this point is reached, something went wrong
        raise Exception(f"Requested shape of invalid input index {ind}")

    # Gets the shape of the output at index ind (there is just one) with folding
    def get_folded_output_shape(self, ind=0):  # noqa, there is just one output
        # Get the unfolded size of the output
        t, d = self.get_normal_output_shape(ind)
        # Get the amount of output (PE) parallelism
        pe = self.get_nodeattr("PE")
        # The output is folded along the embedding dimension, neuron-fold
        nf = d // pe
        # New shape with PE elements as the last dimension
        return tuple((t, nf, pe))
