# Operating system stuff, e.g. paths
import os
# Python warning subsystem
import warnings
# Numpy math and arrays
import numpy as np
# Derive custom operators form the FINN base custom op
from finn.custom_op.fpgadataflow.hlscustomop import HLSCustomOp
# QONNX/FINN datatypes
from qonnx.core.datatype import DataType  # noqa qonnx dependency is specified
# in setup.cfg as well as in fetch-repos.sh


# Scaled Dot-Product Attention Custom Operator
#   Note: Single head attention
class ScaledDotProductAttention(HLSCustomOp):
    # Initializes the operator given an onnx graph node
    def __init__(self, onnx_node, **kwargs):
        # Just forward all arguments to the init method of the CustomOp base
        super().__init__(onnx_node, **kwargs)

    # WIP: Refactor the node attributes matching the HLS operator which is WIP
    # in another repository right now.
    def get_nodeattr_types(self):
        # Start from parent operator class attributes
        attrs = super().get_nodeattr_types()
        # Update attributes dictionary for new custom operator
        attrs.update({
            # Embedding dimension of queries and keys
            "QKDim": ("i", True, 0),
            # Length of the query sequence
            "QLen": ("i", True, 0),
            # Embedding dimension of the values
            "VDim": ("i", True, 0),
            # Length of the key and value sequence
            "KVLen": ("i", True, 0),

            # Folding along the embedding dimensions
            "EmbFold": ("i", True, 0),
            # Folding along the sequence dimensions
            "SeqFold": ("i", True, 0),

            # Datatype of query matrix elements
            "QType": ("s", True, ""),
            # Datatype of key matrix elements
            "KType": ("s", True, ""),
            # Datatype of value matrix elements
            "VType": ("s", True, ""),
            # Datatype of mask matrix elements
            "MType": ("s", False, "INT0"),
            # Datatype of attention weights elements
            "AType": ("s", False, "UINT32"),
            # Datatype of output elements
            "OType": ("s", True, ""),

            # Datatype of accumulator elements of the Query x Key multiplication
            "AccQKMatMul": ("s", False, "UINT32"),
            # Datatype of output elements of the Query x Key multiplication
            "OutQKMatMul": ("s", False, "UINT32"),
            # Activation function type of the Query x Key multiplication
            "ActQKMatMul": ("s", False, "PassThroughActivation<AccQKMatMul>"),

            # Datatype of accumulator elements of the Attention x Value
            # multiplication
            "AccAVMatMul": ("s", False, "UINT32"),
            # Datatype of output elements of the Attention x Value
            # multiplication
            "OutAVMatMul": ("s", False, "UINT32"),
            # Activation function type of the Attention x Value multiplication
            "ActAVMatMul": ("s", False, "PassThroughActivation<AccAVMatMul>"),

            # Activation function type of the softmax normalization of the
            # attention weights
            "ActASoftmax": ("s", False, "PassThroughActivation<OutQKMatMul>"),

            # Mode used for providing the attention mask: There can be no mask,
            # a mask sent as the fourth input or a causal attention mask which
            # is generated by the operator itself.
            "mask_mode": ("s", True, "none", {"none", "input", "causal"}),

            # Execution mode of the operator
            # TODO: Remove once switching to HLSCustomOp
            # TODO: Not possible right now, python mode is still required by
            #  dummy unit test
            "exec_mode": ("s", False, "", {"", "rtlsim", "cppsim", "python"}),
        })
        # Return updated attribute dictionary
        return attrs

    # Shape configuration of the operator
    @property
    def shapes(self):
        # Note: This matches the order of definition above and the order of the
        # HLS lib template as well
        return (self.get_nodeattr("QKDim"), self.get_nodeattr("QLen"),
                self.get_nodeattr("VDim"), self.get_nodeattr("KVLen"))

    # Folding configuration of the operator
    @property
    def folds(self):
        # Note: This matches the order of definition above and the order of the
        # HLS lib template as well
        return self.get_nodeattr("EmbFold"), self.get_nodeattr("SeqFold")

    # Tests whether the given folding is a valid configuration with respect to
    # the shape configuration
    @property
    def is_valid_folding(self):
        # Get and unpack the shape attributes (except the q matrix length, which
        # is never folded)
        qkdim, _, vdim, kvlen = self.shapes
        # Get and unpack the folding attributes
        embfold, seqfold = self.folds
        # All shapes must be multiples of their corresponding fold
        return not ((qkdim % embfold) or (vdim % embfold) or (kvlen % seqfold))

    # Returns an ONNX node that has the same shape inference behavior
    def make_shape_compatible_op(self, model):
        # Infer the output shape from the input shapes
        o_shape = (self.get_nodeattr("QLen"), self.get_nodeattr("VDim"))
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
        if q_dtype != self.get_nodeattr("QType"):
            # Issue a warning message
            warnings.warn("QType changing for %s: %s -> %s " % (
                node.name,
                str(self.get_nodeattr("QType")),
                str(q_dtype),
            ))
        # Test for changing key input datatype
        if k_dtype != self.get_nodeattr("KType"):
            # Issue a warning message
            warnings.warn("KType changing for %s: %s -> %s " % (
                node.name,
                str(self.get_nodeattr("KType")),
                str(k_dtype),
            ))
        # Test for changing value input datatype
        if v_dtype != self.get_nodeattr("VType"):
            # Issue a warning message
            warnings.warn("VType changing for %s: %s -> %s " % (
                node.name,
                str(self.get_nodeattr("VType")),
                str(v_dtype),
            ))

        # Update the node datatype attributes
        self.set_nodeattr("QType", q_dtype.name)
        self.set_nodeattr("KType", k_dtype.name)
        self.set_nodeattr("VType", v_dtype.name)

        # Attention mask might be provided as an input as well
        if self.get_nodeattr("mask_mode") == "input":
            # Get the datatype attribute of the attention mask
            #   Note: Optional mask will be provided as the fourth input
            mask_dtype = model.get_tensor_datatype(node.input[3])
            # Test for changing mask input datatype
            if mask_dtype != self.get_nodeattr("MType"):
                # Issue a warning message
                warnings.warn("MType changing for %s: %s -> %s " % (
                    node.name,
                    str(self.get_nodeattr("MType")),
                    str(mask_dtype),
                ))
            # Update the node datatype attribute of the attention mask
            self.set_nodeattr("MType", mask_dtype.namke)

        # Set the model output datatype
        model.set_tensor_datatype(node.output[0], self.get_nodeattr('OType'))

    # Executes the node
    def execute_node(self, context, graph):
        # The folding configuration must be valid
        assert self.is_valid_folding, "Invalid Folding"

        # The execution mode is configured via a node attribute of the
        # HLSCustomOp base. This is a string, either "cppsim" or "rtlsim".
        mode = self.get_nodeattr("exec_mode")

        # Input data is stored in numpy files in the code generation dictionary
        #   TODO: Refactor this, there is too much duplication of mode checking
        if mode == "cppsim" or mode == "python":
            code_gen_dir = self.get_nodeattr("code_gen_dir_cppsim")
        elif mode == "rtlsim":
            code_gen_dir = self.get_nodeattr("code_gen_dir_ipgen")
        else:
            raise Exception(
                """
                Invalid value for attribute exec_mode! Is currently set to: {}
                has to be set to one of the following value ("cppsim", "rtlsim")
                """.format(mode)
            )

        # Enumerate and name the node inputs. The mask is an optional fourth
        # input. As "zip" runs over the shortest of its arguments, there will be
        # no mask file generated if there is no fourth node input.
        for ind, (name, context_name) in enumerate(
            # TODO: Maybe configure the naming and order of inputs somewhere?
                zip(["q", "k", "v", "m"], self.onnx_node.input)):
            # Read the input from the execution context and reshape to match the
            # expected folding
            x = context[context_name].reshape(self.get_folded_input_shape(ind))
            # TODO: Why do the HLSCustomOp and MatrixVectorActivation make a
            #  copy here?
            # Save the folded inputs to file to be used by simulation
            np.save(os.path.join(code_gen_dir, f"{name}.npy"), x)

        # Support python execution mode for now
        # TODO: Remove python mode once switching to HLSCustomOp
        if mode == "python":
            # Numpy compatible softmax implementation
            from scipy.special import softmax
            # Generate random input data for testing
            from qonnx.util.basic import gen_finn_dt_tensor  # noqa qonnx
            # dependency is specified in setup.cfg as well as in fetch-repos.sh

            # Read input tensors of the query, key and value inputs from context
            q = context[self.onnx_node.input[0]]
            k = context[self.onnx_node.input[1]]
            v = context[self.onnx_node.input[2]]
            # Get the shared embedding dimension of queries and keys
            d = self.get_nodeattr('QKDim')
            # Start with zero mask
            mask = 0
            # The actual attention mask may be provided as the fourth input
            if self.get_nodeattr("mask_mode") == "input":
                # Get the mask tensor from the execution context
                mask = context[self.onnx_node.input[3]]
            # Another option is to generate a causal attention mask on the fly
            elif self.get_nodeattr("mask_mode") == "causal":
                # Get the datatype of the attention mask
                mask_dtype = DataType[self.get_nodeattr("MType")]
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
            # Execute the precompiled C++ simulation program
            # Note: Reusing the HLSCustomOp base implementation is probably fine
            super().exec_precompiled_singlenode_model()
            # Load the output numpy file generated by the C++ simulation
            out = np.load(os.path.join(code_gen_dir, f"out.npy"))
            # Reshape the folded output and insert into the execution context
            context[self.onnx_node.output[0]] = out.reshape(
                self.get_normal_output_shape(ind=0)
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
        inputs = ["Q", "K", "V"]
        # If the attention mask is provided as input, it has a type as well
        if self.get_nodeattr("mask_mode") == "input":
            # The mask type is an attribute itself
            inputs += ["mask"]
        # Look up datatype name in attributes and convert to DataType
        return DataType[self.get_nodeattr(f"{inputs[ind]}Type")]

    # Gets the datatype of the output (at index ind, but there is just one)
    def get_output_datatype(self, ind=0):
        # Ordered list of names of allowed outputs
        outputs = ["O"]
        # Look up datatype name in attributes and convert to DataType
        return DataType[self.get_nodeattr(f"{outputs[ind]}Type")]

    # Gets the shape of the input at index ind without folding
    def get_normal_input_shape(self, ind=0):
        # List shapes of inputs in order
        inputs_shapes = [
            # Query input sequence
            (self.get_nodeattr("QLen"), self.get_nodeattr("QKDim")),
            # Key input sequence
            (self.get_nodeattr("KVLen"), self.get_nodeattr("QKDim")),
            # Value input sequence
            (self.get_nodeattr("KVLen"), self.get_nodeattr("VDim")),
        ]
        # If the attention mask is provided as input, it has a shape as well
        if self.get_nodeattr("mask_mode") == "input":
            # Mask shape is inferred from query and key sequence lengths
            inputs_shapes += [
                (self.get_nodeattr("QLen"), self.get_nodeattr("KVLen"))
            ]
        # Get the shape by indexing into the ordered list of all inputs
        return inputs_shapes[ind]

    # Gets the shape of the output at index ind (there is just one) without
    # folding
    def get_normal_output_shape(self, ind=0):  # noqa, there is just one output
        # The output shape is inferred from the length of the query sequence and
        # the embedding dimension of the values
        return tuple((self.get_nodeattr("QLen"), self.get_nodeattr("VDim")))

    # Gets the shape of the attention weights at index ind (there is just one)
    # without folding
    def get_normal_attention_shape(self, ind=0):  # noqa, there is just one
        # The attention weights have shape covering both sequence dimensions
        return tuple((self.get_nodeattr("QLen"), self.get_nodeattr("KVLen")))

    # Gets the shape of the input at index ind with folding
    def get_folded_input_shape(self, ind=0):
        # Get the unfolded size of the input
        ilen, idim = self.get_normal_input_shape(ind)
        # Get the folding configuration specifying the amount of parallelism
        embfold, seqfold = self.folds

        # Queries, keys and values are all folded similarly along the embedding
        # dimension
        if ind in (0, 1, 2):
            # Note: Embedding dimension is always assumed to be the second
            # dimension, any transpose is handled implicitly by the operator
            return ilen, embfold, idim // embfold

        # If the mask is provided as input, it is folded along the second
        # sequence dimension
        if ind == 3 and self.get_nodeattr("mask_mode") == "input":
            # Note: Both dimensions are sequence dimension, the second
            # corresponds to the KVLen
            return ilen, seqfold, idim // seqfold

        # If this point is reached, something went wrong
        raise Exception(f"Requested shape of invalid input index {ind}")

    # Gets the shape of the output at index ind (there is just one) with folding
    def get_folded_output_shape(self, ind=0):  # noqa, there is just one output
        # Get the unfolded size of the output
        olen, odim = self.get_normal_output_shape(ind)
        # Get the folding configuration specifying the amount of parallelism
        embfold, seqfold = self.folds
        # The output is always folded along the embedding dimension, which is
        # assumed to be the second dimension
        return olen, embfold, odim // embfold

    # Gets the shape of the attention weights at index ind (there is just one)
    # with folding
    def get_folded_attention_shape(self, ind=0):  # noqa, there is just one
        # Get the unfolded size of the attention weights
        alen, adim = self.get_normal_attention_shape(ind)
        # Get the folding configuration specifying the amount of parallelism
        embfold, seqfold = self.folds
        # The attention weights are always folded along the sequence dimension,
        # which is assumed to be the second dimension
        return alen, seqfold, adim // seqfold

    # Widths of the input data stream of the input at index ind
    def get_instream_width(self, ind=0):
        # Get the number of bits used to represent the input
        i_bits = self.get_input_datatype(ind).bitwidth()
        # Parallelism is the number of elements in the last dimension of the
        # folded input
        _, _, elems = self.get_folded_input_shape(ind)
        # Width of a stream receiving input elements in parallel
        return elems * i_bits

    # Widths of the output data stream of the output at index ind
    def get_outstream_width(self, ind=0):
        # Get the number of bits used to represent the output
        o_bits = self.get_output_datatype(ind).bitwidth()
        # Parallelism is the number of elements in the last dimension of the
        # folded output
        _, _, elems = self.get_folded_output_shape(ind)
        # Width of a stream producing output elements in parallel
        return elems * o_bits

    # Maximum width of any ap_int used in this operator
    def get_ap_int_max_w(self):
        # Find the widths of the widest input
        i_bits_max = max((self.get_instream_width(ind) for ind in range(3)))
        # Find the widths of the widest output
        o_bits_max = max((self.get_outstream_width(ind) for ind in range(1)))
        # Assume no bits to represent the mask, if there is no mask
        m_bits = 0
        # A mask received as input or produced as causal on the fly has a
        # bit-width as well
        if self.get_nodeattr("mask_mode") in {"input", "causal"}:
            # Parallelism is the number of elements in the last dimension of the
            # folded mask input
            _, _, elems = self.get_folded_input_shape(ind=3)
            # Get width of the mask datatype
            m_bits = elems * DataType[self.get_nodeattr("MType")].bitwidth()

        # Elements per folded key input (second input)
        _, _, i_elems = self.get_folded_input_shape(ind=1)
        # Elements per folded value input (third input), same as the number of
        # output elements
        _, _, o_elems = self.get_folded_input_shape(ind=2)

        # Parallelism is the number of elements in the last dimension of the
        # folded attention weights
        _, _, s_elems = self.get_folded_attention_shape()
        # Number of bits used for the attention weights stream
        a_bits = s_elems * DataType[self.get_nodeattr("AType")].bitwidth()

        # Maximum bits per tile of the key and value matrix streams
        tile_bits_max = max([
            i_elems * s_elems * DataType[self.get_nodeattr("KType")].bitwidth(),
            o_elems * s_elems * DataType[self.get_nodeattr("VType")].bitwidth(),
        ])
        # Maximum bits per matmul accumulators
        acc_bits_max = max([
            # These are not streamed, thus single element width is counted
            DataType[self.get_nodeattr("AccQKMatMul")].bitwidth(),
            DataType[self.get_nodeattr("AccAVMatMul")].bitwidth(),
        ])
        # Maximum bits per matmul outputs
        out_bits_max = max([
            # These are the stream widths, which are always >= than individual
            # elements
            s_elems * DataType[self.get_nodeattr("OutQKMatMul")].bitwidth(),
            o_elems * DataType[self.get_nodeattr("OutAVMatMul")].bitwidth(),
        ])
        # Aggregate the maximum bit width in both matmul operators over all
        # inputs, intermediates and outputs
        matmul_bits_max = max([
            tile_bits_max, acc_bits_max, out_bits_max
        ])

        # Find maximum of all (maximal) bit-widths
        return max([i_bits_max, o_bits_max, m_bits, a_bits, matmul_bits_max])

    # Minimize the accumulator bit width
    def minimize_accumulator_width(self, model):  # noqa: model is unused
        # Ge the query, key, value and attention weights type
        QType = DataType[self.get_nodeattr("QType")]  # noqa
        KType = DataType[self.get_nodeattr("KType")]  # noqa
        VType = DataType[self.get_nodeattr("VType")]  # noqa
        AType = DataType[self.get_nodeattr("AType")]  # noqa
        # Minimal and maximal possible results of query-key multiplication
        qk_min = self.get_nodeattr("QKDim") * QType.min() * KType.min()
        qk_max = self.get_nodeattr("QKDim") * QType.max() * KType.max()
        # Minimal and maximal possible results of attention-value multiplication
        av_min = self.get_nodeattr("VDim") * AType.min() * VType.min()
        av_max = self.get_nodeattr("VDim") * AType.max() * VType.max()
        # Update the accumulator types to fit the min-max range
        #   TODO: Is this correct?
        _qk_max = max(-qk_min, 1 + qk_max)
        acc_bit_width = np.log2(_qk_max) + 1
        acc_bit_width = int(np.ceil(acc_bit_width))
        self.set_nodeattr("AccQKMatMul", f"UINT{acc_bit_width}")
        _av_max = max(-av_min, 1 + av_max)
        acc_bit_width = np.log2(_av_max) + 1
        acc_bit_width = int(np.ceil(acc_bit_width))
        self.set_nodeattr("AccAVMatMul", f"UINT{acc_bit_width}")

    # Gets the number of expected output values, i.e. how many times read()
    # could/should be called on the output stream of this operator
    def get_number_output_values(self):
        # Elements over all but the last dimension of the output folded along
        # the embedding dimension
        return np.prod(self.get_folded_output_shape()[:-1])

    # Generates list of C++ includes to be placed at the top of the generated
    # code
    def global_includes(self):
        # FINN HLSLIB activation functions: e.g. PassThroughActivation
        self.code_gen_dict["$GLOBALS$"] = ['#include "activations.hpp"']
        # Attention operator HLS code
        self.code_gen_dict["$GLOBALS$"] += ['#include "attention.hpp"']

    # Generates C++ code of type alias, global constant and macro definitions
    def defines(self, var):
        # Generate shape definitions from attributes to C++ constant definitions
        def shapedefs(*names):
            # C++ qualified type to be used for shape constants
            shape = "static constexpr std::size_t"
            # Generate a C++ constant definition for each of the attributes
            # given by argument list names
            return (
                f"{shape} {name} = {self.get_nodeattr(name)};" for name in names
            )

        # Generate datatype definitions mapping from QONNX DataType to HLS type
        def typedefs(*names):
            # Gets the HLS type string for the datatype specified by the named
            # attribute
            def hls_type(name):
                # Looks up the datatype specified for the attribute and
                # translates from QONNX to HLS type
                return DataType[self.get_nodeattr(name)].get_hls_datatype_str()

            # Generate a C++ type alias definition for each of the attributes
            # given by argument list names
            return (f"using {name} = {hls_type(name)};" for name in names)

        # Insert constants and type aliases into the dictionary
        self.code_gen_dict["$DEFINES$"] = [
            # Shape constant definitions of attention inputs (query, key and
            # value) and folding configuration
            *shapedefs(
                "QKDim",
                "QLen",
                "VDim",
                "KVLen",
                "EmbFold",
                "SeqFold"
            ),
            # Type alias definitions for all input, output and intermediate
            # datatypes
            *typedefs(
                "QType",
                "KType",
                "VType",
                "MType",
                "AType",
                "OType"
            ),
            # Type alias definitions for the matmul accumulators and output
            # datatypes
            *typedefs(
                "AccQKMatMul",
                "OutQKMatMul",
                "AccAVMatMul",
                "OutAVMatMul"
            ),
            # Type alias definitions for the activation functions
            f"using ActQKMatMul = {self.get_nodeattr('ActQKMatMul')};",
            f"using ActAVMatMul = {self.get_nodeattr('ActAVMatMul')};",
            f"using ActASoftmax = {self.get_nodeattr('ActASoftmax')};",
            # Type alias of the properly configured attention operator class
            f"using Attention = ScaledDotProductAttention<",
            f"    QKDim,",
            f"    QLen,",
            f"    VDim,",
            f"    KVLen,",
            f"    EmbFold,",
            f"    SeqFold,",
            f"    QType,",
            f"    KType,",
            f"    VType,",
            f"    MType,",
            f"    AType,",
            f"    OType,",  # Note: OType and last MatMul out must match
            f"    AccQKMatMul,",
            f"    OutQKMatMul,",
            f"    ActQKMatMul,",
            f"    AccAVMatMul,",
            f"    OType,",  # Note: OType and last MatMul out must match
            f"    ActAVMatMul,",
            f"    ActASoftmax",
            f">;",
            # Short type aliases of attention input and output streams
            f"using QStream = Attention::QStream;",
            f"using KStream = Attention::KStream;",
            f"using VStream = Attention::VStream;",
            f"using OStream = Attention::OStream;",
            f"using MStream = Attention::MStream;",
        ]

    # Generates C++ code for reading data from .npy (numpy format) for testing
    # in C++ simulation
    def read_npy_data(self):
        # Input data is stored in numpy files in the code generation dictionary
        code_gen_dir = self.get_nodeattr("code_gen_dir_cppsim")

        # Generate function calls for reading the input files into the input
        # streams
        self.code_gen_dict["$READNPYDATA$"] = [
            # Deduce the datatype of elements packed into the query input stream
            #   TODO: Maybe these type-deductions can be removed by changing the
            #    order of the template arguments of the npy2apintstream, such
            #    that type-deduction is handled there?
            f'using QPacked = decltype(QStream{{}}.read());',
            # Generate function call reading from file into the input stream
            #   Note: Inputs are always represented as numpy floats
            f'npy2apintstream<QPacked, QType, QType::width, float>(',
            f'  "{code_gen_dir}/q.npy", q, false',
            ');',

            # Deduce the datatype of elements packed into the key input stream
            f'using KPacked = decltype(KStream{{}}.read());',
            # Generate function call reading from file into the input stream
            #   Note: Inputs are always represented as numpy floats
            f'npy2apintstream<KPacked, KType, KType::width, float>(',
            f'  "{code_gen_dir}/k.npy", k, false',
            ');',

            # Deduce the datatype of elements packed into the value input stream
            f'using VPacked = decltype(VStream{{}}.read());',
            # Generate function call reading from file into the input stream
            #   Note: Inputs are always represented as numpy floats
            f'npy2apintstream<VPacked, VType, VType::width, float>(',
            f'  "{code_gen_dir}/v.npy", v, false',
            ');',
        ]

        # If the mask is provided as an input, it needs to be read as well
        if self.get_nodeattr("mask_mode") == "input":
            # Generate function call for reading the mask file into the input
            # stream
            self.code_gen_dict["$READNPYDATA$"] += [
                # Deduce the datatype of elements packed into the mask input
                # stream
                f'using MPacked = decltype(MStream{{}}.read());',
                # Generate function call reading from file into the input stream
                #   Note: Inputs are always represented as numpy floats
                f'npy2apintstream<MPacked, MType, MType::width, float>(',
                f'  "{code_gen_dir}/m.npy", m, false',
                ');',
            ]

    # Generates C++ code for declaring all streams involved in C++ simulation
    # for testing
    def strm_decl(self):
        # Declare input (query, key, value) and output streams
        self.code_gen_dict["$STREAMDECLARATIONS$"] = [
            # Note: Assumes stream type aliases to be set in defines
            'QStream q;', 'KStream k;', 'VStream v;', 'OStream out;'
        ]
        # If the mask is provided as an input, it needs a stream declaration as
        # well
        if self.get_nodeattr("mask_mode") == "input":
            # Append the mask stream to the declaration list
            self.code_gen_dict["$STREAMDECLARATIONS$"] += [
                # Note: Assumes stream type aliases to be set in defines
                'MStream m;',
            ]

    # Generates C++ code for calling the computation part of the operator
    def docompute(self):
        # Write the body of the attention top-level function
        self.code_gen_dict["$DOCOMPUTE$"] = [
            # Instantiate the attention operator and connect to the streams
            # Note: Assumes "Attention" to be aliased appropriate configuration
            #   in defines with.
            "Attention attention(q, k, v);",
            # Transfer from input to output stream
            # TODO: Get rid of this once switching to function-call style for
            #  the attention operator.
            "for(std::size_t i = 0; i < QLen * EmbFold; ++i) {",
            "    out.write(attention.out.read());",
            "}",
        ]

    # Generates C++ code for reading the output stream and converting back to
    # numpy format for testing in C** simulation
    def dataoutstrm(self):
        # Output data will be stored in numpy files in the code generation
        # dictionary
        code_gen_dir = self.get_nodeattr("code_gen_dir_cppsim")
        # Get the expected shape of the folded output array formatted as a C++
        # vector initializer
        # Note: Valid formatting relies on correct placement of curly braces
        # and line breaks: Open/close all three braces on the same line of code
        # to avoid '\n' to be inserted into the string
        shape = f"""{{{
        ','.join((str(i) for i in self.get_folded_output_shape()))
        }}}"""
        # Generate function call for reading from the output stream into the
        # output file
        self.code_gen_dict["$DATAOUTSTREAM$"] = [
            # Deduce the datatype of elements packed into the output stream
            f'using OPacked = decltype(OStream{{}}.read());',
            # Generate function call reading from stream into the output file
            #   Note: Outputs are always represented as numpy floats
            f'apintstream2npy<OPacked, OType, OType::width, float>(',
            f'  out, {shape}, "{code_gen_dir}/out.npy", false',
            ');',
        ]

    # Generates C++ code for saving the output of C++ simulation to a file in
    # numpy format
    def save_as_npy(self):
        # Note: This seems to be empty in ALL HLSCustomOps. Probably it was used
        # for something before, which is now integrated into dataoutstrm()?
        self.code_gen_dict["$SAVEASCNPY$"] = []

    # Generates essentially the head of the C++ function from which the IP block
    # will be generated during ipgen, i.e. actual synthesis
    def blackboxfunction(self):
        # Insert function head describing the top level interface of the
        # attention operator
        self.code_gen_dict["$BLACKBOXFUNCTION$"] = [
            # Note: Assumes stream type aliases to be set in defines
            f"void {self.onnx_node.name} (",
            f"    QStream &q, KStream &k, VStream &v, OStream &out",
            f")",
        ]

    # Generates C++ pragmas to be inserted into the main function of the C++
    # simulation and the ipgen-blackboxfunction as well
    def pragmas(self):
        # Add HLS interface directives specifying how to create RTL ports for
        # the top-level function arguments
        self.code_gen_dict["$PRAGMAS$"] = [
            # Connect the query input stream with an axi stream interface
            "#pragma HLS INTERFACE axis port=q",
            # Connect the key input stream with an axi stream interface
            "#pragma HLS INTERFACE axis port=k",
            # Connect the value input stream with an axi stream interface
            "#pragma HLS INTERFACE axis port=v",
            # Connect the output stream with an axi stream interface
            "#pragma HLS INTERFACE axis port=out",
        ]
        # No block-level I/O protocol for the function return value
        self.code_gen_dict["$PRAGMAS$"].append(
            "#pragma HLS INTERFACE ap_ctrl_none port=return"
        )
