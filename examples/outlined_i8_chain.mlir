module {
  func.func @forward(%a: tensor<3x17xi8>, %b: tensor<17x65xi8>, %bias:tensor<65xi32>) -> tensor<3x65xi32> attributes {llvm.emit_c_interface} {
    %zero = arith.constant 0 : i32
    %empty = tensor.empty() : tensor<3x65xi32>
    %init = linalg.fill ins(%zero : i32) outs(%empty : tensor<3x65xi32>) -> tensor<3x65xi32>
    %result = linalg.generic {indexing_maps=[affine_map<(d0,d1,d2)->(d0,d2)>,affine_map<(d0,d1,d2)->(d2,d1)>,affine_map<(d0,d1,d2)->(d0,d1)>], iterator_types=["parallel","parallel","reduction"]}
      ins(%a,%b : tensor<3x17xi8>,tensor<17x65xi8>) outs(%init : tensor<3x65xi32>) {
    ^bb0(%x:i8,%y:i8,%acc:i32):
      %xx = arith.extsi %x : i8 to i32
      %yy = arith.extsi %y : i8 to i32
      %p = arith.muli %xx,%yy : i32
      %s = arith.addi %acc,%p : i32
      linalg.yield %s : i32
    } -> tensor<3x65xi32>
    %bias_empty=tensor.empty():tensor<3x65xi32>
    %final=linalg.generic {indexing_maps=[affine_map<(i,j)->(i,j)>,affine_map<(i,j)->(j)>,affine_map<(i,j)->(i,j)>],iterator_types=["parallel","parallel"]}
    ins(%result,%bias:tensor<3x65xi32>,tensor<65xi32>) outs(%bias_empty:tensor<3x65xi32>) {
    ^bb0(%x:i32,%y:i32,%o:i32):
      %sum=arith.addi %x,%y:i32
      linalg.yield %sum:i32
    }->tensor<3x65xi32>
    func.return %final:tensor<3x65xi32>
  }
}
